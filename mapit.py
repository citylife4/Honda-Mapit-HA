import datetime, hashlib, hmac, requests
from settings import *
import json
from pymongo import MongoClient
import oracledb
import time
import logging
import argparse
import os

class RequestFailedException(Exception):
    pass

class TokenExpiredException(Exception):
    pass

class Mapit:
  
  def __init__(self, username, password, mappit_identityPoolId, mappit_userPoolId, mappit_userPoolWebClientId, oracle_user, oracle_password, oracle_dns, logger, mongo_url="mongodb://localhost:27017/", debug=False, skip_db_init=False):
    self.logger = logger
    self.debug = debug
    self.try_count = 0
    self.identityPoolId = mappit_identityPoolId
    self.userPoolId = mappit_userPoolId
    self.userPoolWebClientId = mappit_userPoolWebClientId
    self.authenticationFlowType = "USER_PASSWORD_AUTH"
    self.service = 'execute-api'
    self.host = 'core.prod.mapit.me'
    self.region = 'eu-west-1'
    self.url_idp = f"cognito-idp.{self.region}.amazonaws.com"
    self.url_identity = f"cognito-identity.{self.region}.amazonaws.com"
    self.mongoUrl = mongo_url
    self.logger.debug("Mapit initialized with username: %s", username)
    self.username = username
    self.password = password
    self.oracle_user = oracle_user
    self.oracle_password = oracle_password
    self.oracle_dns = oracle_dns
    
    # Initialize database connections (can be deferred)
    self._oracle_conn = None
    self._mongo_client = None
    self._mongo_db = None
    self._mongo_collection = None
    
    if not skip_db_init:
      self._init_oracle_connection()
      # Oracle connection is lazy - only connect when needed
    
    self.getAllTokens(username, password)

  def _init_oracle_connection(self):
    """Initialize Oracle database connection with timeout."""
    try:
      mypath = os.path.dirname(os.path.realpath(__file__))
      self._oracle_conn = oracledb.connect(
        config_dir=f"{mypath}/wallet",
        user=self.oracle_user,
        password=self.oracle_password,
        dsn=self.oracle_dns,
        wallet_location=f"{mypath}/wallet",
        wallet_password=self.oracle_password,
        tcp_connect_timeout=10  # 10 second timeout
      )
      self.logger.info("Successfully connected to Oracle Database")
      self._ensure_oracle_table()
    except Exception as e:
      self.logger.error("Failed to connect to Oracle Database: %s", e)
      self._oracle_conn = None

  def _init_mongo_connection(self):
    """Initialize MongoDB connection."""
    try:
      self._mongo_client = MongoClient(self.mongoUrl, serverSelectionTimeoutMS=5000)
      self._mongo_db = self._mongo_client['mapit']
      self._mongo_collection = self._mongo_db['speed']
      self.logger.info("Successfully connected to MongoDB")
    except Exception as e:
      self.logger.error("Failed to connect to MongoDB: %s", e)
      self._mongo_client = None

  def _ensure_oracle_table(self):
    """Create vehicle_data table if it doesn't exist."""
    if not self._oracle_conn:
      return
    cursor = self._oracle_conn.cursor()
    try:
      # Create MAPIT schema/table for vehicle tracking
      # Using a more organized table name for multi-use database
      cursor.execute('''create table MAPIT_VEHICLE_TRACKING (
          id number generated always as identity,
          lng NUMBER(10, 7), 
          lat NUMBER(10, 7), 
          speed NUMBER(6, 2), 
          status VARCHAR2(50),
          battery NUMBER(3),
          hdop NUMBER(6, 2),
          odometer NUMBER(10, 2),
          last_coord_ts NUMBER(13),
          creation_ts timestamp with time zone default current_timestamp,
          primary key (id)
      )''')
      self._oracle_conn.commit()
      self.logger.info("Created MAPIT_VEHICLE_TRACKING table")
    except oracledb.DatabaseError as e:
      if "ORA-00955" in str(e):  # Table already exists
        self.logger.debug("Table MAPIT_VEHICLE_TRACKING already exists")
      else:
        self.logger.warning("Table creation issue: %s", e)

  def close_connections(self):
    """Close all database connections."""
    if self._oracle_conn:
      self._oracle_conn.close()
      self.logger.debug("Oracle connection closed")
    if self._mongo_client:
      self._mongo_client.close()
      self.logger.debug("MongoDB connection closed")

  def _ensure_oracle_connected(self):
    """Ensure Oracle connection is established (lazy initialization)."""
    if self._oracle_conn is None:
      self._init_oracle_connection()
    return self._oracle_conn is not None

  def storeOracle(self, response):
    """Store vehicle data in Oracle database."""
    if not self._ensure_oracle_connected():
      self.logger.error("Oracle connection not available")
      return False
    
    self.logger.info("Storing data in Oracle DB: %s", response)
    cursor = self._oracle_conn.cursor()
    
    # Insert data into the table
    data = [response]
    cursor.executemany(
      "INSERT INTO MAPIT_VEHICLE_TRACKING (lng, lat, speed, status, battery, hdop, odometer, last_coord_ts) "
      "VALUES (:lng, :lat, :speed, :status, :battery, :hdop, :odometer, :last_coord_ts)", 
      data
    )
    self._oracle_conn.commit()
    
    self.logger.info("Data stored in Oracle DB successfully")
    return True

  def storeMongo(self, response):
    """Store vehicle data in MongoDB."""
    if not self._mongo_client:
      self.logger.error("MongoDB connection not available")
      return False
    
    self.logger.debug("Storing data in MongoDB: %s", response)
    # Copy to avoid mutating original dict
    to_store = response.copy()
    to_store["datetime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    self._mongo_collection.insert_one(to_store)
    
    self.logger.info("Data stored in MongoDB successfully")
    return True

  def sign(self, key, msg):
    self.logger.debug("Signing message: %s", msg)
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

  def getSignatureKey(self, key, dateStamp):
    self.logger.debug("Getting signature key for date: %s", dateStamp)
    kDate = self.sign(('AWS4' + key).encode('utf-8'), dateStamp)
    kRegion = self.sign(kDate, self.region)
    kService = self.sign(kRegion, self.service)
    kSigning = self.sign(kService, 'aws4_request')
    return kSigning

  def createAuthValue(self, method, spacename, canonical_querystring='' ):
    self.logger.debug("Creating auth value for method: %s, spacename: %s", method, spacename)
    t = datetime.datetime.now()
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    datestamp = t.strftime('%Y%m%d')
    canonical_headers = 'accept:' + 'application/json' + '\n' 'host:' + self.host + '\n' + 'x-amz-date:' + amz_date + '\n'
    signed_headers = 'accept;host;x-amz-date'
    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = datestamp + '/' + self.region + '/' + self.service + '/' + 'aws4_request'
    payload_hash = hashlib.sha256(('').encode('utf-8')).hexdigest()
    canonical_request = method + '\n' + spacename + '\n' + canonical_querystring + '\n' + canonical_headers + '\n' + signed_headers + '\n' + payload_hash
    string_to_sign = algorithm + '\n' + amz_date + '\n' + credential_scope + '\n' + hashlib.sha256(
        canonical_request.encode('utf-8')).hexdigest()
    signing_key = self.getSignatureKey(self.secret_key, datestamp)
    signature = hmac.new(signing_key, (string_to_sign).encode("utf-8"), hashlib.sha256).hexdigest()
    Auth_value = f"AWS4-HMAC-SHA256 Credential={self.access_key}/{datestamp}/{self.region}/{self.service}/aws4_request, SignedHeaders=accept;host;x-amz-date Signature={signature}"
    return Auth_value, amz_date

  def sendRequest(self, url, headers, contentype='application/x-amz-json-1.1', method='POST', payload=None, url_type='https://'):
    self.logger.debug("Sending request to URL: %s with headers: %s and payload: %s", url, headers, payload)
    headers['content-type'] = contentype
    response = requests.request(method, url_type+url, headers=headers, json=payload)
    ## Check if the response is 200
    if self.debug:
      if self.try_count > 10:
        self.try_count = 0
        raise TokenExpiredException("Token expired")
      self.try_count += 1
    if response.status_code == 403:
      print("Token expired. Getting new tokens")
      raise TokenExpiredException("Token expired")
    if response.status_code != 200:
      print(f"Error on {url}: ", response.status_code)
      print(response.text)
      raise RequestFailedException(f"Error on request: {response.status_code}")
    return response.json()
  
  def getTokens(self, username, password):
    self.logger.debug("Getting tokens for username: %s", username)
    payload = { "AuthFlow": self.authenticationFlowType,
                "ClientId": self.userPoolWebClientId,
                "AuthParameters": {"USERNAME": username,"PASSWORD": password},    
                "ClientMetadata": {}}
    headers = {'x-amz-target': 'AWSCognitoIdentityProviderService.InitiateAuth'}

    response = self.sendRequest(self.url_idp, headers, payload=payload)
    self.idToken = response['AuthenticationResult']['IdToken']
    self.accessToken = response['AuthenticationResult']['AccessToken']
    return response
     
  def getIdentity(self):
    self.logger.debug("Getting identity")
    payload = {  
      "IdentityPoolId": self.identityPoolId, 
      "Logins":{f"{self.url_idp}/{self.userPoolId}": self.idToken }}
    headers = {'x-amz-target': 'AWSCognitoIdentityService.GetId'}

    response = self.sendRequest(self.url_identity, headers, payload=payload)
    self.identityId = response['IdentityId']
    return response
  
  def getCredentials(self):
    self.logger.debug("Getting credentials")
    payload = {"IdentityId":self.identityId,
               "Logins":{f"{self.url_idp}/{self.userPoolId}":self.idToken}}
    headers = {'x-amz-target': 'AWSCognitoIdentityService.GetCredentialsForIdentity'}

    response = self.sendRequest(self.url_identity, headers, payload=payload)
    self.access_key = response['Credentials']['AccessKeyId']
    self.secret_key = response['Credentials']['SecretKey']
    self.sessionToken = response['Credentials']['SessionToken']
    return response
  
  def getUser(self):
    self.logger.debug("Getting user information")
    payload = {"AccessToken":self.accessToken}
    headers = {'x-amz-target': 'AWSCognitoIdentityProviderService.GetUser'}

    user = self.sendRequest(self.url_idp, headers, payload=payload)
    return user

  def authorizedRequest(self, spacename, canonical_querystring='', method='GET', payload=None):
    self.logger.debug("Making authorized request to spacename: %s", spacename)
    Auth_value, amz_date = self.createAuthValue(method, spacename, canonical_querystring)
    url = self.host + spacename + '?' + canonical_querystring
    headers = {
        'host': self.host,
        'Accept': 'application/json',
        'X-Amz-Security-Token': self.sessionToken,
        'X-Id-Token': self.idToken,
        'x-amz-date': amz_date,
        'Authorization': Auth_value,
    }
    return self.sendRequest(url, headers, method=method, payload=payload)
  
  def getId(self, account):
    self.logger.debug("Getting account ID")

    # Preferred path: derive account ID from authorized vehicle list
    try:
      vehicles_response = self.authorizedRequest('/v1/vehicles')
      vehicles = vehicles_response.get('data', [])
      if vehicles and isinstance(vehicles[0], dict):
        account_info = vehicles[0].get('account', {})
        account_id = account_info.get('id') if isinstance(account_info, dict) else None
        if account_id:
          self.id = account_id
          return vehicles_response
      self.logger.warning("Could not resolve account ID from /v1/vehicles response")
    except RequestFailedException as err:
      self.logger.warning("Account ID lookup via /v1/vehicles failed: %s", err)

    # Legacy fallback path used by older API behavior
    self.logger.debug("Falling back to /v1/accounts lookup for account ID")
    spacename = '/v1/accounts'
    canonical_querystring = 'email=' + account
    response = self.authorizedRequest(spacename, canonical_querystring)
    self.id = response[0]['id']
    return response
  
  def getSummary(self):
    self.logger.debug("Getting summary for ID: %s", self.id)
    spacename = '/v1/accounts/' + self.id + '/summary'

    try:
      response = self.authorizedRequest(spacename)
    except TokenExpiredException:
      self.logger.info("Token expired, refreshing tokens")
      self.generateTokens(self.username, self.password)
      self.store_tokens_to_file()
      response = self.authorizedRequest(spacename)
      self.logger.debug("Token refreshed")
    return response
  
  def generateTokens(self, username, password):
    self.TokensResponse = self.getTokens(username, password)
    self.IdentityResponse = self.getIdentity()
    self.CredentialsResponse = self.getCredentials()
    self.IdResponse = self.getId(username.replace('@', '%40')) 
  
  def getAllTokens(self, username, password):
    self.logger.debug("Getting all tokens for username: %s", username)
    if os.path.exists('tokens.json'):
      self.load_tokens_from_file()
    else:
      self.generateTokens(username, password)
      self.store_tokens_to_file()

  def store_tokens_to_file(self):
    tokens = {
      'access_key': self.access_key,
      'secret_key': self.secret_key,
      'session_token': self.sessionToken,
      'identity_id': self.identityId,
      'id_token': self.idToken,
      'access_token': self.accessToken,
      'id': self.id
    }
    with open('tokens.json', 'w') as f:
      json.dump(tokens, f)
    self.logger.debug("Tokens stored to file successfully")

  def load_tokens_from_file(self):
    with open('tokens.json', 'r') as f:
      tokens = json.load(f)
    self.access_key = tokens['access_key']
    self.secret_key = tokens['secret_key']
    self.sessionToken = tokens['session_token']
    self.identityId = tokens['identity_id']
    self.idToken = tokens['id_token']
    self.accessToken = tokens['access_token']
    self.id = tokens['id']
    self.logger.debug("Tokens loaded from file successfully")

  
  def checkStatus(self):
    """Get current vehicle status (lng, lat, speed, status)."""
    self.logger.debug("Checking if moving")
    response = self.getSummary()
    state = response['vehicles'][0]['device']['state']
    lng = state['lng']
    lat = state['lat']
    speed = state['speed']
    status = state['status']
    
    # Normalize speed: set to 0 when vehicle is at rest
    # API sometimes reports residual speed values when stopped
    if status == 'AT_REST':
      speed = 0
    
    return lng, lat, speed, status, response

  def get_history_from_oracle(self, limit=100):
    """Query historical location data from Oracle database."""
    if not self._ensure_oracle_connected():
      self.logger.error("Oracle connection not available")
      return []
    
    cursor = self._oracle_conn.cursor()
    cursor.execute(
      "SELECT lng, lat, speed, status, battery, hdop, odometer, last_coord_ts, creation_ts "
      "FROM MAPIT_VEHICLE_TRACKING ORDER BY creation_ts DESC FETCH FIRST :limit ROWS ONLY",
      {"limit": limit}
    )
    rows = cursor.fetchall()
    
    history = []
    for row in rows:
      history.append({
        "lng": float(row[0]) if row[0] else 0,
        "lat": float(row[1]) if row[1] else 0,
        "speed": row[2],
        "status": row[3],
        "battery": row[4],
        "hdop": row[5],
        "odometer": row[6],
        "last_coord_ts": row[7],
        "timestamp": row[8].isoformat() if row[8] else None
      })
    
    self.logger.debug("Retrieved %d historical records", len(history))
    return history

  def export_geojson(self, filepath, limit=1000):
    """Export location history as GeoJSON file."""
    history = self.get_history_from_oracle(limit)
    
    if not history:
      self.logger.warning("No historical data to export")
      return False
    
    # Create GeoJSON FeatureCollection
    features = []
    coordinates = []
    
    for point in reversed(history):  # Chronological order
      # Point feature
      features.append({
        "type": "Feature",
        "geometry": {
          "type": "Point",
          "coordinates": [point["lng"], point["lat"]]
        },
        "properties": {
          "speed": point["speed"],
          "status": point["status"],
          "timestamp": point["timestamp"]
        }
      })
      coordinates.append([point["lng"], point["lat"]])
    
    # Add LineString for the path
    if len(coordinates) > 1:
      features.append({
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": coordinates
        },
        "properties": {
          "name": "Vehicle Path",
          "points": len(coordinates)
        }
      })
    
    geojson = {
      "type": "FeatureCollection",
      "features": features
    }
    
    with open(filepath, 'w') as f:
      json.dump(geojson, f, indent=2)
    
    self.logger.info("Exported %d points to %s", len(history), filepath)
    return True

  def export_kml(self, filepath, limit=1000):
    """Export location history as KML file."""
    try:
      import simplekml
    except ImportError:
      self.logger.error("simplekml not installed. Run: pip install simplekml")
      return False
    
    history = self.get_history_from_oracle(limit)
    
    if not history:
      self.logger.warning("No historical data to export")
      return False
    
    kml = simplekml.Kml(name="Vehicle Tracking History")
    
    # Add folder for points
    folder = kml.newfolder(name="Location Points")
    
    coordinates = []
    for point in reversed(history):  # Chronological order
      coords = (point["lng"], point["lat"])
      coordinates.append(coords)
      
      # Add placemark
      pnt = folder.newpoint(
        name=f"{point['status']} - {point['speed']} km/h",
        coords=[coords]
      )
      pnt.description = f"Time: {point['timestamp']}\nSpeed: {point['speed']} km/h\nStatus: {point['status']}"
      
      # Color based on status
      if point["status"] == "MOVING":
        pnt.style.iconstyle.color = simplekml.Color.green
      else:
        pnt.style.iconstyle.color = simplekml.Color.red
    
    # Add path as LineString
    if len(coordinates) > 1:
      line = kml.newlinestring(name="Vehicle Path")
      line.coords = coordinates
      line.style.linestyle.width = 3
      line.style.linestyle.color = simplekml.Color.blue
    
    kml.save(filepath)
    self.logger.info("Exported %d points to %s", len(history), filepath)
    return True


def setup_logging(level=logging.INFO):
    """Configure logging with specified level."""
    logging.basicConfig(
        level=level,
        format='[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
        datefmt='%H:%M:%S %d-%m-%Y'
    )
    logger = logging.getLogger()
    return logger


def run_continuous(mapit, logger, interval=5):
    """Run in continuous mode - poll and log every interval seconds."""
    seconds = 0
    try:
        while True:
            lng, lat, speed, status, response = mapit.checkStatus()
            logger.info(f"Summary retrieved at {seconds}s: {lng}, {lat}, {status} at {speed} km/h")
            seconds += interval
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    finally:
        mapit.close_connections()


def run_checker(mapit, logger, sleep_time=1):
    """Run in checker mode - only store when position changes."""
    try:
        last_lng, last_lat = 0, 0
        while True:
            lng, lat, speed, status, response = mapit.checkStatus()
            if (lng, lat) != (last_lng, last_lat):
                logger.info(f"Vehicle moved: {lng}, {lat} at {speed} km/h (was: {last_lng}, {last_lat})")
                
                # Extract additional fields from response
                state = response['vehicles'][0]['device']['state']
                vehicle = response['vehicles'][0]
                
                data = {
                    "lng": str(lng), 
                    "lat": str(lat), 
                    "speed": str(speed), 
                    "status": status,
                    "battery": state.get('battery'),
                    "hdop": state.get('hdop'),
                    "odometer": state.get('odometer'),
                    "last_coord_ts": state.get('lastCoordTs')
                }
                
                mapit.storeOracle(data)
                last_lng, last_lat = lng, lat
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    finally:
        mapit.close_connections()


def run_single_query(mapit, logger):
    """Run single query and display summary."""
    try:
        response = mapit.getSummary()
        logger.info(json.dumps(response, indent=2))
    finally:
        mapit.close_connections()


def run_map_server(mapit, logger, port=5000, refresh_rate=5):
    """Start Flask web server with live map."""
    from map_server import create_app
    
    app = create_app(mapit, refresh_rate)
    logger.info(f"Starting map server on http://localhost:{port}")
    logger.info(f"Map will refresh every {refresh_rate} seconds")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("Map server stopped by user")
    finally:
        mapit.close_connections()


def run_export_geojson(mapit, logger, filepath):
    """Export location history to GeoJSON file."""
    try:
        if mapit.export_geojson(filepath):
            logger.info(f"Successfully exported to {filepath}")
        else:
            logger.error("Export failed")
    finally:
        mapit.close_connections()


def run_export_kml(mapit, logger, filepath):
    """Export location history to KML file."""
    try:
        if mapit.export_kml(filepath):
            logger.info(f"Successfully exported to {filepath}")
        else:
            logger.error("Export failed")
    finally:
        mapit.close_connections()


def create_mapit_instance(args, logger):
    """Create and return a Mapit instance with configuration from settings."""
    return Mapit(
        username=mappit_username,
        password=mappit_password,
        mappit_identityPoolId=mappit_identityPoolId,
        mappit_userPoolId=mappit_userPoolId,
        mappit_userPoolWebClientId=mappit_userPoolWebClientId,
        oracle_user=oracle_user,
        oracle_password=oracle_password,
        oracle_dns=oracle_dns,
        logger=logger,
        mongo_url=getattr(__import__('settings'), 'mongo_url', 'mongodb://localhost:27017/'),
        debug=args.debug
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Mapit Vehicle Tracking - Extract and visualize motorcycle location data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mapit.py                           # Single query, display summary
  python mapit.py --continuous              # Poll every 5 seconds
  python mapit.py --checker --sleep-time 10 # Store when position changes
  python mapit.py --serve-map --map-port 8080 --refresh-rate 10
  python mapit.py --export-geojson path.geojson
  python mapit.py --export-kml path.kml
        """
    )
    
    # General options
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    # Operation modes (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--continuous', action='store_true', 
                            help='Poll continuously every 5 seconds')
    mode_group.add_argument('--checker', action='store_true', 
                            help='Store to Oracle only when position changes')
    mode_group.add_argument('--serve-map', action='store_true', 
                            help='Start Flask web server with live map')
    mode_group.add_argument('--export-geojson', type=str, metavar='FILE',
                            help='Export location history to GeoJSON file')
    mode_group.add_argument('--export-kml', type=str, metavar='FILE',
                            help='Export location history to KML file')
    
    # Mode-specific options
    parser.add_argument('--sleep-time', type=int, default=1, 
                        help='Seconds between polls in checker mode (default: 1)')
    parser.add_argument('--map-port', type=int, default=5000, 
                        help='Port for Flask map server (default: 5000)')
    parser.add_argument('--refresh-rate', type=int, default=5, 
                        help='Map auto-refresh interval in seconds (default: 5)')
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(logging.DEBUG if args.debug else logging.INFO)
    logger.debug("Starting Mapit...")
    
    # Create Mapit instance
    mapit = create_mapit_instance(args, logger)
    
    # Run appropriate mode
    if args.continuous:
        run_continuous(mapit, logger)
    elif args.checker:
        run_checker(mapit, logger, args.sleep_time)
    elif args.serve_map:
        run_map_server(mapit, logger, args.map_port, args.refresh_rate)
    elif args.export_geojson:
        run_export_geojson(mapit, logger, args.export_geojson)
    elif args.export_kml:
        run_export_kml(mapit, logger, args.export_kml)
    else:
        run_single_query(mapit, logger)
