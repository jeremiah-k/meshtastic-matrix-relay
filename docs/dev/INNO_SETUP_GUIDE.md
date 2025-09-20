# Inno Setup Modification Guide for mmrelay.iss

## Overview

This guide provides comprehensive instructions for safely modifying the `scripts/mmrelay.iss` file, which is critical for building the Windows installer. **This file is extremely sensitive** - incorrect modifications will break the CI build process.

## ⚠️ Critical Warning

**Always ask for feedback before committing changes to mmrelay.iss**. This file has caused multiple build failures due to:

- Pascal syntax differences from other languages
- String handling peculiarities
- Procedure vs function confusion
- YAML generation complexity

## File Structure

The mmrelay.iss file contains several key sections:

### 1. Setup Section `[Setup]`

- Basic installer configuration
- App name, version, directories
- **Safe to modify**: Basic metadata, paths, filenames

### 2. Files Section `[Files]`

- Defines which files to include in installer
- **Rarely needs modification**

### 3. Icons Section `[Icons]`

- Start menu shortcuts and icons
- **Safe to modify**: Icon names, descriptions

### 4. Run Section `[Run]`

- Post-install actions
- **Safe to modify**: Descriptions, flags

### 5. Code Section `[Code]`

- **MOST DANGEROUS SECTION** - Contains Pascal script
- Configuration file generation logic
- User input validation
- **Requires extreme care when modifying**

## Pascal Language Specifics

### String Handling

```pascal
// CORRECT - Double quotes for most YAML values (escape " and \ if present)
SafeHome := HomeserverURL;
StringChangeEx(SafeHome, '"', '\\"', True);
StringChangeEx(SafeHome, '\\', '\\\\', True);
config := 'matrix:' + #13#10 +
          '  homeserver: "' + SafeHome + '"' + #13#10;

// ALSO VALID - Single quotes require escaping internal single quotes (''), but
// are preferred for Windows paths and secrets to avoid backslash/escape issues.
config := 'matrix:' + #13#10 +
          '  homeserver: ''' + EscapedURL + '''' + #13#10;
```

### Procedures vs Functions

```pascal
// StringChange and StringChangeEx are PROCEDURES (modify in-place)
// CORRECT usage:
SafePwd := MatrixPage.Values[2];
StringChangeEx(SafePwd, '''', '''''', True);
config := config + '  password: ''' + SafePwd + '''' + #13#10;

// WRONG - Cannot use procedures in expressions:
config := '  password: ''' + StringChange(pwd, '''', '''''') + '''' + #13#10;
//                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
//                            This causes "Type mismatch" error
```

### Line Endings

- Use `#13#10` for Windows line endings (CRLF).
- YAML accepts LF or CRLF; CRLF keeps files Windows‑friendly.

### String Concatenation

```pascal
// CORRECT - Multi-line concatenation
config := 'matrix:' + #13#10 +
          '  homeserver: "' + HomeserverURL + '"' + #13#10 +
          '  bot_user_id: "' + bot_user_id + '"' + #13#10;

// Each line must end with + for continuation
```

## Common Modification Scenarios

### Adding New Configuration Fields

1. **Add to wizard page creation** (in `InitializeWizard` procedure)
2. **Add validation** (in `NextButtonClick` function)
3. **Add to config generation** (in `AfterInstall` procedure)

Example:

```pascal
// 1. Add to wizard page
MatrixPage := CreateInputQueryPage(wpWelcome,
  'Matrix Configuration', 'Configure Matrix connection',
  'Please enter your Matrix server details:');
MatrixPage.Add('Homeserver URL:', False);
MatrixPage.Add('Bot User ID:', False);
MatrixPage.Add('Password:', True);
MatrixPage.Add('New Field:', False);  // ADD THIS

// 2. Add validation
if Trim(MatrixPage.Values[3]) = '' then  // NEW FIELD INDEX
begin
  MsgBox('New field cannot be empty.', mbError, MB_OK);
  Result := False;
  Exit;
end;

// 3. Add to config generation
config := config + '  new_field: "' + MatrixPage.Values[3] + '"' + #13#10;
```

### Modifying YAML Output

**Prefer double quotes** for most YAML values. **Use single quotes** for:

- Windows paths containing backslashes (avoid escapes like \e),
- Secrets (passwords), to minimize quoting pitfalls.
  When using single quotes, escape internal single quotes by doubling them.

```pascal
// Double-quoted YAML: escape backslashes then quotes
SafeValue := value;
StringChangeEx(SafeValue, '\\', '\\\\', True);  // \ -> \\\
StringChangeEx(SafeValue, '"', '\\"', True);      // "
config := config + '  field: "' + SafeValue + '"' + #13#10;

// Single-quoted (escape internal ' as '') — safer for Windows paths/secrets
config := config + '  field: ''' + EscapedValue + '''' + #13#10;
```

### Adding Validation

```pascal
// String validation
if Trim(SomeValue) = '' then
begin
  MsgBox('Field cannot be empty.', mbError, MB_OK);
  Abort;  // Stops installation
end;

// Format validation
if Pos('@', UserID) <> 1 then
begin
  MsgBox('User ID must start with @', mbError, MB_OK);
  Result := False;  // In NextButtonClick function
  Exit;
end;
```

## Testing Strategy

### 1. Local Testing

- Install Inno Setup (includes ISCC.exe) on Windows.
- From repo root: `"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" scripts\mmrelay.iss`
- Use this to validate syntax/quoting before pushing to CI.

### 2. CI Testing

- Every change triggers Windows installer build
- Build failure indicates syntax or logic errors
- Check CI logs for specific error messages

### 3. Incremental Changes

- Make small, focused changes
- Test each change individually
- Commit frequently with descriptive messages

## Common Errors and Solutions

### "Type mismatch" Error

**Cause**: Using procedures as functions
**Solution**: Use procedures correctly (modify variables in-place)

```pascal
// WRONG
result := StringChange(input, 'old', 'new');

// CORRECT
temp := input;
StringChangeEx(temp, 'old', 'new', True);
result := temp;
```

### "Invalid number of parameters" Error

**Cause**: Incorrect function/procedure signature
**Solution**: Check Inno Setup documentation for correct parameters

### YAML Syntax Errors

**Cause**: Incorrect quoting or escaping
**Solution**: Prefer double quotes; use single quotes for Windows paths and secrets (escape internal single quotes by doubling)

## Documentation References

- [Inno Setup Help](https://jrsoftware.org/ishelp/)
- [Pascal Scripting Reference](https://jrsoftware.org/ishelp/topic_scriptfunctions.htm)
- [StringChangeEx Documentation](https://jrsoftware.org/ishelp/topic_isxfunc_stringchangeex.htm)

## Best Practices

1. **Make minimal changes** — don't rewrite working code
2. **Quoting** — prefer double quotes; use single quotes for Windows paths/secrets
3. **Test string handling carefully** — Pascal differs from many languages
4. **Understand procedure vs function** differences
5. **Check CI builds immediately** after pushing changes
6. **Reference official documentation** for unfamiliar functions
7. **Keep changes focused** — one logical change per commit

## Emergency Recovery

If you break the build:

1. **Identify the exact error** from CI logs
2. **Revert to last working version** if needed
3. **Make minimal fix** addressing only the specific error
4. **Test immediately** with new CI build

## File Locations

- **Main file**: `scripts/mmrelay.iss`
- **CI workflow**: `.github/workflows/build.yml`
- **Build artifacts**: Generated in CI, downloadable from Actions tab

Remember: **This file is critical infrastructure**. When in doubt, ask for help rather than guessing.

## Detailed Code Analysis

### Current Configuration Generation Logic

The `AfterInstall` procedure generates a complete `config.yaml` file. Here's the flow:

```pascal
procedure AfterInstall(const InstallDir: string);
var
  config: string;
  SafePwd: string;
  // ... other variables
begin
  // 1. Extract and validate homeserver URL
  HomeserverURL := Trim(MatrixPage.Values[0]);
  ServerName := ExtractHostFromURL(HomeserverURL);

  // 2. Process and validate bot user ID
  bot_user_id := Trim(MatrixPage.Values[1]);
  // Complex logic to handle @user:server vs user formats

  // 3. Build Matrix section with single-quoted values (escape internal ' as '')
  config := 'matrix:' + #13#10 +
            '  homeserver: ''' + HomeserverURL + '''' + #13#10 +
            '  bot_user_id: ''' + bot_user_id + '''' + #13#10;

  // 4. Add password if provided (uses single quotes with escaping)
  if MatrixPage.Values[2] <> '' then
  begin
    SafePwd := MatrixPage.Values[2];
    StringChangeEx(SafePwd, '''', '''''', True); // Escape single quotes
    config := config + '  password: ''' + SafePwd + '''' + #13#10;
  end;

  // 5. Add matrix_rooms and meshtastic sections
  // ... continues with room and connection configuration
end;
```

### Key Variables and Their Sources

| Variable             | Source                           | Validation             | Usage              |
| -------------------- | -------------------------------- | ---------------------- | ------------------ |
| `HomeserverURL`      | `MatrixPage.Values[0]`           | URL format check       | Matrix homeserver  |
| `bot_user_id`        | `MatrixPage.Values[1]`           | MXID format validation | Matrix bot user    |
| `SafePwd`            | `MatrixPage.Values[2]`           | Optional, escaped      | Matrix password    |
| `room_id`            | `MatrixMeshtasticPage.Values[0]` | Required, format check | Matrix room        |
| `meshtastic_channel` | `MatrixMeshtasticPage.Values[1]` | Defaults to '0'        | Meshtastic channel |
| `connection_type`    | `MeshtasticPage.Values[0]`       | Must be serial/tcp/ble | Connection method  |

### String Escaping Rules

#### For YAML Double-Quoted Values (Escaping Required)

Double-quoted YAML interprets escape sequences. You must escape embedded `"` and backslashes `\` to avoid generating invalid YAML.

```pascal
// Escape embedded double quotes and backslashes
SafeValue := rawValue;
StringChangeEx(SafeValue, '"', '\\"', True);  // " inside YAML
StringChangeEx(SafeValue, '\\', '\\\\', True);  // \ inside YAML
config := config + '  field: "' + SafeValue + '"' + #13#10;
```

Tip: For Windows paths and raw strings, prefer single-quoted YAML (see next section) to avoid extensive escaping.

#### For YAML Single-Quoted Values (Complex)

```pascal
// Must escape single quotes by doubling them
SafeValue := rawValue;
StringChangeEx(SafeValue, '''', '''''', True);
config := config + '  field: ''' + SafeValue + '''' + #13#10;
```

#### For Pascal String Literals

```pascal
// Single quotes in Pascal strings must be doubled
message := 'Can''t do this';  // Results in: Can't do this
```

## Advanced Modification Examples

### Adding a New Optional Field

```pascal
// 1. Add to wizard page (in InitializeWizard)
MatrixPage.Add('Optional Field:', False);

// 2. Add to config generation (in AfterInstall)
if Trim(MatrixPage.Values[4]) <> '' then  // Assuming index 4
  config := config + '  optional_field: "' + MatrixPage.Values[4] + '"' + #13#10;
```

### Adding Complex Validation

```pascal
// In NextButtonClick function
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = MatrixPage.ID then
  begin
    // Validate homeserver URL
    if (Pos('http://', LowerCase(Trim(MatrixPage.Values[0]))) <> 1) and
       (Pos('https://', LowerCase(Trim(MatrixPage.Values[0]))) <> 1) then
    begin
      MsgBox('Homeserver URL must start with http:// or https://', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    // Validate user ID format
    if (Trim(MatrixPage.Values[1]) = '') then
    begin
      MsgBox('Bot User ID cannot be empty.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;
```

### Adding Conditional Configuration

```pascal
// Example: Add E2EE configuration based on checkbox
if OptionsPage.Values[0] then  // E2EE enabled checkbox
begin
  config := config + 'matrix:' + #13#10 +
            '  e2ee:' + #13#10 +
            '    enabled: true' + #13#10 +
            // Use single quotes to preserve backslashes in Windows paths
            '    store_path: ''' + InstallDir + '\e2ee_store''' + #13#10;
end;
```

## Troubleshooting Guide

### Build Error: "Type mismatch"

**Symptoms**: CI build fails with "Type mismatch" error on specific line
**Common Causes**:

1. Using procedure as function: `StringChange(...)` in expression
2. Wrong parameter types: passing string where integer expected
3. Assignment type mismatch: assigning function result to wrong type

**Solutions**:

```pascal
// WRONG - procedure used as function
result := StringChange(input, 'old', 'new');

// CORRECT - procedure used properly
temp := input;
StringChangeEx(temp, 'old', 'new', True);
result := temp;
```

### Build Error: "Invalid number of parameters"

**Symptoms**: Function call has wrong number of arguments
**Solution**: Check Inno Setup documentation for correct function signature

### Build Error: "Undeclared identifier"

**Symptoms**: Variable or function name not recognized
**Common Causes**:

1. Typo in variable name
2. Variable not declared in `var` section
3. Function name misspelled

### Runtime Error: Invalid YAML

**Symptoms**: Installer completes but generated config.yaml is malformed
**Common Causes**:

1. Missing quotes around string values
2. Incorrect indentation
3. Special characters not escaped

**Prevention**:

- Prefer double quotes; use single quotes for Windows paths and secrets (escape internal ' by doubling)
- Test with various input combinations
- Validate YAML structure manually

## Historical Issues and Lessons

### Issue #1: StringChange Function Misuse (2024)

**Problem**: Used `StringChange()` as function in string concatenation
**Error**: "Type mismatch" on line 218, column 76
**Root Cause**: `StringChange` is procedure, not function
**Solution**: Reverted to double-quote approach, avoiding escaping entirely
**Lesson**: Always check if identifier is procedure or function

### Issue #2: YAML Quoting Complexity

**Problem**: Complex single-quote escaping led to errors
**Solution**: Standardized on double quotes for most YAML values
**Lesson**: Simplicity reduces error potential

### Issue #3: Variable Scope Issues

**Problem**: Variables declared in wrong scope, causing compilation errors
**Solution**: Proper variable declaration in appropriate `var` sections
**Lesson**: Understand Pascal scoping rules

## Code Review Checklist

Before committing changes to mmrelay.iss:

- [ ] **Syntax Check**: All procedures/functions used correctly
- [ ] **String Handling**: Consistent quoting strategy (prefer double quotes)
- [ ] **Variable Scope**: All variables properly declared
- [ ] **Error Handling**: Appropriate validation and error messages
- [ ] **YAML Structure**: Generated YAML will be valid
- [ ] **Backward Compatibility**: Changes don't break existing functionality
- [ ] **Documentation**: Complex changes documented in comments
- [ ] **Testing Plan**: Know how to verify changes work correctly

## Quick Reference

### Essential Pascal Syntax

```pascal
// Variable declaration
var
  myString: string;
  myInteger: Integer;
  myBoolean: Boolean;

// String concatenation
result := 'Hello' + ' ' + 'World';

// Conditional
if condition then
begin
  // multiple statements
end
else
  // single statement

// Line endings for YAML
#13#10  // Windows CRLF
```

### Common Inno Setup Functions

```pascal
// String manipulation
Trim(s)                    // Remove whitespace
Copy(s, start, length)     // Substring
Pos(substr, s)            // Find position
LowerCase(s)              // Convert to lowercase

// User interaction
MsgBox(text, type, buttons)  // Show message box
Abort                        // Stop installation

// File operations
FileExists(filename)         // Check if file exists
ExpandConstant('{app}')     // Expand Inno Setup constant
```

Remember: **Every change to mmrelay.iss should be minimal, well-tested, and thoroughly documented.**

## Windows Service Management

### Overview

MM Relay can be run as a Windows service for automatic startup and background operation. This section provides comprehensive guidance for service installation, management, and troubleshooting.

### Service Installation Commands

#### Using NSSM (Non-Sucking Service Manager)

**Recommended approach** - NSSM provides robust service management:

```batch
# Install MM Relay as a service using NSSM
nssm install MMRelay "C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml"

# Set service description
nssm set MMRelay Description "Matrix <> Meshtastic Relay Service"

# Set service to auto-start
nssm set MMRelay Start SERVICE_AUTO_START

# Start the service
nssm start MMRelay
```

#### Using Windows SC Command

**Alternative approach** - Built-in Windows service control:

```batch
# Install service (requires full paths)
sc create MMRelay binPath= "\"C:\Program Files\MM Relay\mmrelay.exe\" --config \"C:\Program Files\MM Relay\config.yaml\"" DisplayName= "Matrix <> Meshtastic Relay" start= auto

# Set service description
sc description MMRelay "Matrix <> Meshtastic Relay Service"

# Start the service
net start MMRelay
```

### Service Management Commands

#### Basic Service Operations

```batch
# Start service
net start MMRelay
nssm start MMRelay

# Stop service
net stop MMRelay
nssm stop MMRelay

# Restart service
net stop MMRelay && net start MMRelay
nssm restart MMRelay

# Check service status
sc query MMRelay
nssm status MMRelay

# Delete service (service must be stopped first)
sc delete MMRelay
nssm remove MMRelay confirm
```

#### Advanced Service Configuration

```batch
# Set service recovery options (using NSSM)
nssm set MMRelay AppExit Default Exit
nssm set MMRelay AppRestartDelay 5000

# Set service dependencies (using SC)
sc config MMRelay depend= Tcpip/Dnscache

# Set service logon account
sc config MMRelay obj= "NT AUTHORITY\LocalService"
sc config MMRelay obj= ".\LocalUser" password= "password"
```

### Service Troubleshooting

#### Common Issues and Solutions

**Service fails to start:**

```batch
# Check service error code
sc query MMRelay

# View Windows Event Log
eventvwr.msc
# Look for Application logs with source "MMRelay" or "Service Control Manager"

# Test service manually
"C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml" --log-level debug
```

**Permission issues:**

```batch
# Check service account permissions
sc qc MMRelay

# Test with different service account
sc config MMRelay obj= "NT AUTHORITY\LocalService"
net stop MMRelay && net start MMRelay
```

**Configuration file access issues:**

```batch
# Verify config file exists and is accessible
dir "C:\Program Files\MM Relay\config.yaml"
type "C:\Program Files\MM Relay\config.yaml"

# Test config file syntax
"C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml" validate
```

### Service Configuration

#### Configuration File Location

Service configuration should be stored in a secure, accessible location:

```yaml
# Recommended config file location: C:\Program Files\MM Relay\config.yaml
# Ensure the service account has read access to this file

matrix:
  homeserver: "https://matrix.org"
  bot_user_id: "@mmrelay:matrix.org"
  password: "your_password"

meshtastic:
  connection_type: "network"
  host: "localhost"
  meshnet_name: "MyMeshNet"
  broadcast_enabled: true

logging:
  level: "info"
  file: "C:\Program Files\MM Relay\logs\mmrelay.log"
```

#### Service-Specific Configuration

```yaml
# Add service-specific settings to config.yaml
service:
  enabled: true
  name: "MMRelay"
  display_name: "Matrix <> Meshtastic Relay"
  description: "Relays messages between Matrix and Meshtastic networks"
  
# Configure logging for service environment
logging:
  level: "info"
  file: "C:\Program Files\MM Relay\logs\service.log"
  max_size: "10MB"
  backup_count: 5
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

### Service Logs Location

#### Default Log Locations

```batch
# Application logs (Windows Event Log)
eventvwr.msc
# Navigate to: Windows Logs -> Application
# Filter by source: "MMRelay"

# File-based logs (if configured in config.yaml)
dir "C:\Program Files\MM Relay\logs\"
type "C:\Program Files\MM Relay\logs\mmrelay.log"

# NSSM service logs (if using NSSM)
dir "C:\Program Files\MM Relay\logs\"
type "C:\Program Files\MM Relay\logs\MMRelay.log"
```

#### Log Configuration

```yaml
# Configure comprehensive logging in config.yaml
logging:
  level: "info"  # debug, info, warning, error, critical
  file: "C:\Program Files\MM Relay\logs\mmrelay.log"
  max_size: "10MB"
  backup_count: 5
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  
  # Console logging (useful for debugging)
  console: true
  console_level: "info"
  
  # Error logging to Windows Event Log
  event_log: true
  event_log_source: "MMRelay"
```

### Service Startup Type

#### Configuring Startup Behavior

```batch
# Auto-start service when system boots
sc config MMRelay start= auto

# Manual start only
sc config MMRelay start= demand

# Disabled (won't start automatically)
sc config MMRelay start= disabled

# Delayed auto-start (for better system boot performance)
sc config MMRelay start= delayed-auto
```

#### Startup Dependencies

```batch
# Set service dependencies (service will wait for these to start)
sc config MMRelay depend= Tcpip/Dnscache/EventLog

# Remove all dependencies
sc config MMRelay depend= 

# Check current dependencies
sc qc MMRelay
```

### Service Dependencies

#### Network Dependencies

```batch
# Ensure network services are available before starting
sc config MMRelay depend= Tcpip/Dnscache

# For services that require internet connectivity
sc config MMRelay depend= Tcpip/Dnscache/EventLog

# For services that depend on specific network adapters
sc config MMRelay depend= Tcpip/Dnscache/Netman
```

#### Application Dependencies

```batch
# If MM Relay depends on other services
sc config MMRelay depend= Tcpip/Dnscache/OtherServiceName

# Check dependency status
sc query MMRelay
sc query OtherServiceName
```

### Service Security Context

#### Service Account Configuration

```batch
# Local System account (full system access)
sc config MMRelay obj= "NT AUTHORITY\LocalSystem"

# Local Service account (limited access, recommended)
sc config MMRelay obj= "NT AUTHORITY\LocalService"

# Network Service account (network access)
sc config MMRelay obj= "NT AUTHORITY\NetworkService"

# Custom user account
sc config MMRelay obj= ".\ServiceUser" password= "SecurePassword"
```

#### Permission Requirements

```batch
# Grant read access to config file
icacls "C:\Program Files\MM Relay\config.yaml" /grant "LocalService":R

# Grant write access to log directory
icacls "C:\Program Files\MM Relay\logs" /grant "LocalService":W

# Grant modify access to data directory
icacls "C:\Program Files\MM Relay\data" /grant "LocalService":M
```

### Service Recovery Options

#### Automatic Recovery Configuration

```batch
# Using NSSM for advanced recovery options
nssm set MMRelay AppExit Default Exit
nssm set MMRelay AppRestartDelay 5000
nssm set MMRelay AppThrottle 15000

# Using SC for basic recovery
sc failure MMRelay reset= 86400 actions= restart/60000/restart/60000/restart/60000
```

#### Recovery Actions

```batch
# Configure service to restart on failure
sc failure MMRelay command= "C:\Program Files\MM Relay\restart.bat" reset= 86400 actions= restart/60000/restart/60000/restart/60000

# Create restart.bat file
@echo off
echo Restarting MM Relay service at %DATE% %TIME% >> "C:\Program Files\MM Relay\logs\restart.log"
net stop MMRelay
timeout /t 5
net start MMRelay
```

### Service Removal

#### Safe Service Removal

```batch
# Stop service first
net stop MMRelay

# Remove service
sc delete MMRelay

# Using NSSM
nssm stop MMRelay
nssm remove MMRelay confirm
```

#### Cleanup After Removal

```batch
# Remove service-related files
rd /s /q "C:\Program Files\MM Relay\logs"
rd /s /q "C:\Program Files\MM Relay\data"

# Remove registry entries (if any)
reg delete "HKLM\SYSTEM\CurrentControlSet\Services\MMRelay" /f
```

### Service Debugging

#### Debug Mode Configuration

```batch
# Create debug batch file
@echo off
echo Starting MM Relay in debug mode...
"C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml" --log-level debug --console

# Run service interactively for debugging
"C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml" --log-level debug
```

#### Debug Logging

```yaml
# Configure debug logging in config.yaml
logging:
  level: "debug"
  file: "C:\Program Files\MM Relay\logs\debug.log"
  console: true
  console_level: "debug"
  
  # Enable detailed component logging
  components:
    matrix: "debug"
    meshtastic: "debug"
    plugins: "debug"
    database: "debug"
```

### Service Performance

#### Performance Monitoring

```batch
# Monitor service resource usage
tasklist /fi "imagename eq mmrelay.exe" /v

# Monitor service performance counters
typeperf "\Process(mmrelay)\*" -sc 10

# Check service memory usage
wmic process where "name='mmrelay.exe'" get WorkingSetSize,PageFileUsage
```

#### Performance Optimization

```yaml
# Configure performance settings in config.yaml
performance:
  max_memory_usage: "512MB"
  max_cpu_usage: 80
  connection_pool_size: 10
  message_queue_size: 1000
  
  # Throttling settings
  rate_limit:
    enabled: true
    messages_per_second: 100
    burst_size: 1000
```

### Service Monitoring

#### Health Checks

```batch
# Create health check script
@echo off
sc query MMRelay | find "RUNNING" > nul
if %errorlevel% equ 0 (
    echo Service is running
    exit 0
) else (
    echo Service is not running
    exit 1
)
```

#### Monitoring Scripts

```batch
# Monitor service and restart if needed
@echo off
:loop
sc query MMRelay | find "RUNNING" > nul
if %errorlevel% neq 0 (
    echo Service is down, restarting at %DATE% %TIME% >> "C:\Program Files\MM Relay\logs\monitor.log"
    net start MMRelay
)
timeout /t 60
goto loop
```

### Service Backup

#### Backup Configuration

```batch
# Create backup script
@echo off
set backup_dir="C:\MMRelay_Backup\%DATE:/=-%"
mkdir %backup_dir%

# Backup configuration
copy "C:\Program Files\MM Relay\config.yaml" %backup_dir%\

# Backup logs
xcopy "C:\Program Files\MM Relay\logs" %backup_dir%\logs\ /E /I

# Backup data
xcopy "C:\Program Files\MM Relay\data" %backup_dir%\data\ /E /I

echo Backup completed at %DATE% %TIME% >> %backup_dir%\backup.log
```

#### Automated Backup

```batch
# Schedule daily backup
schtasks /create /tn "MMRelay Backup" /tr "C:\Program Files\MM Relay\backup.bat" /sc daily /st 02:00 /ru "SYSTEM"

# View backup schedule
schtasks /query /tn "MMRelay Backup"
```

### Service Migration

#### Migration to New Server

```batch
# Export service configuration
sc qc MMRelay > "C:\MMRelay_Backup\service_config.txt"

# Backup all data
robocopy "C:\Program Files\MM Relay" "C:\MMRelay_Backup" /E /COPYALL

# On new server:
# 1. Install application
# 2. Restore configuration
# 3. Recreate service using exported config
```

#### Migration Steps

```batch
# Step 1: Stop and backup current service
net stop MMRelay
sc qc MMRelay > "C:\MMRelay_Backup\service_config.txt"
robocopy "C:\Program Files\MM Relay" "C:\MMRelay_Backup" /E /COPYALL

# Step 2: Install on new server
# Run installer on new server

# Step 3: Restore configuration
robocopy "C:\MMRelay_Backup" "C:\Program Files\MM Relay" /E /COPYALL

# Step 4: Create service on new server
nssm install MMRelay "C:\Program Files\MM Relay\mmrelay.exe" --config "C:\Program Files\MM Relay\config.yaml"

# Step 5: Start service
net start MMRelay
```

### Best Practices for Windows Services

1. **Use Local Service account** for better security
2. **Configure proper logging** for troubleshooting
3. **Set up recovery options** for automatic restart
4. **Monitor service health** regularly
5. **Backup configuration** before making changes
6. **Test service manually** before installing as service
7. **Document all changes** to service configuration
8. **Use NSSM** for advanced service management features
9. **Configure appropriate dependencies** for reliable startup
10. **Implement monitoring** for production environments
