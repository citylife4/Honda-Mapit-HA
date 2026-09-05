# HACS Deployment Guide

This document describes how to submit and maintain the Mapit Motorcycle Tracker integration in HACS (Home Assistant Community Store).

## Prerequisites

- Integration published on GitHub
- Working GitHub Actions for validation
- Valid `hacs.json` file in repository root
- Valid `manifest.json` in integration directory

## Submitting to HACS

### 1. Prepare Repository

Ensure your repository meets HACS requirements:

✅ `hacs.json` in repository root
✅ `manifest.json` with required fields:
  - domain
  - name
  - version
  - documentation
  - issue_tracker
  - codeowners
  - iot_class
  - config_flow

✅ GitHub Actions for validation:
  - HACS validation workflow
  - Hassfest validation
  - Python syntax tests

### 2. Submit to HACS Default Repository

1. **Fork the HACS repository**: https://github.com/hacs/default

2. **Add your integration** to the appropriate file:
   - Edit `integrations` (for integrations)
   - Add entry:
   ```json
   {
     "name": "citylife4/Honda-Mapit-HA"
   }
   ```

3. **Create Pull Request** with:
   - Title: "Add Mapit Motorcycle Tracker integration"
   - Description: Brief overview of the integration

4. **Wait for validation**:
   - HACS bot will validate your repository
   - Fix any issues reported
   - PR will be reviewed by HACS maintainers

### 3. Alternative: Custom Repository

Users can add as custom repository while waiting for HACS approval:

1. In Home Assistant, go to HACS
2. Click on the 3 dots in top right corner
3. Select "Custom repositories"
4. Add URL: `https://github.com/citylife4/Honda-Mapit-HA`
5. Select category: "Integration"
6. Click "Add"

## Release Process

### Creating a Release

1. **Update version** in `manifest.json`:
   ```json
   "version": "1.1.0"
   ```

2. **Commit and push** changes:
   ```bash
   git add custom_components/mapit_tracker/manifest.json
   git commit -m "Bump version to 1.1.0"
   git push
   ```

3. **Create GitHub release**:
   - Go to Releases on GitHub
   - Click "Draft a new release"
   - Create tag: `v1.1.0` (must start with 'v')
   - Release title: `v1.1.0`
   - Add release notes describing changes
   - Click "Publish release"

4. **Automated release workflow**:
   - GitHub Actions will automatically:
     - Update manifest version
     - Create release archive (`mapit_tracker.zip`)
     - Upload to release assets
     - Update release notes with installation instructions

### Release Notes Template

```markdown
## What's Changed

### New Features
- Feature 1 description
- Feature 2 description

### Bug Fixes
- Fix 1 description
- Fix 2 description

### Breaking Changes
- Breaking change 1 (if any)

**Full Changelog**: https://github.com/citylife4/Honda-Mapit-HA/compare/v1.0.0...v1.1.0
```

## HACS Updates

Once in HACS, updates are automatic:

1. User installs integration via HACS
2. HACS monitors repository for new releases
3. When new release is published, HACS shows update available
4. User clicks update in HACS
5. Integration is automatically updated

## GitHub Actions

### Test Workflow (`.github/workflows/test.yml`)

Runs on every push and PR:
- Python syntax validation
- JSON validation
- Module import tests
- Manifest structure validation

### HACS Validation (`.github/workflows/hacs.yml`)

Validates HACS compatibility:
- HACS action validation
- Hassfest validation (Home Assistant)
- Ensures integration meets requirements

### Release Workflow (`.github/workflows/release.yml`)

Triggered on release creation:
- Updates manifest version
- Creates release archive
- Uploads assets
- Updates release notes

## Validation Checklist

Before submitting to HACS:

- [ ] All GitHub Actions passing
- [ ] `hacs.json` present and valid
- [ ] `manifest.json` complete with all required fields
- [ ] README.md with installation instructions
- [ ] At least one release published
- [ ] Integration tested in Home Assistant
- [ ] No hard-coded credentials or secrets
- [ ] Follows Home Assistant coding standards

## Troubleshooting

### HACS Validation Fails

1. Check GitHub Actions logs
2. Ensure all required files present
3. Validate JSON files syntax
4. Check manifest.json has all required fields

### Hassfest Fails

1. Review Hassfest output
2. Check import statements
3. Validate requirements in manifest.json
4. Ensure proper async/await usage

### Release Upload Fails

1. Verify GITHUB_TOKEN permissions
2. Check release tag format (must start with 'v')
3. Ensure workflow has proper permissions

## Maintenance

### Regular Updates

- Monitor issues for bug reports
- Update dependencies when needed
- Keep documentation current
- Test with latest Home Assistant releases

### Version Numbering

Follow Semantic Versioning:
- MAJOR.MINOR.PATCH (e.g., 1.2.3)
- MAJOR: Breaking changes
- MINOR: New features (backward compatible)
- PATCH: Bug fixes (backward compatible)

## Resources

- [HACS Documentation](https://hacs.xyz/)
- [HACS Integration Requirements](https://hacs.xyz/docs/publish/integration)
- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [Hassfest Validation](https://developers.home-assistant.io/blog/2020/04/16/hassfest/)

## Support

For HACS-related issues:
- HACS Discord: https://discord.gg/apgchf8
- HACS Discussions: https://github.com/hacs/integration/discussions

For integration issues:
- GitHub Issues: https://github.com/citylife4/Honda-Mapit-HA/issues
