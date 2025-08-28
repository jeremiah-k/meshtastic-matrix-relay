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
// CORRECT - Double quotes for most YAML values (watch for embedded " and backslashes)
config := 'matrix:' + #13#10 +
          '  homeserver: "' + HomeserverURL + '"' + #13#10;

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

- Use `#13#10` for Windows line endings (CRLF)
- Required for proper YAML formatting

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
// CORRECT - Double quotes for most values
config := config + '  field: "' + value + '"' + #13#10;

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
**Solution**: Use double quotes consistently

## Documentation References

- [Inno Setup Help](https://jrsoftware.org/ishelp/)
- [Pascal Scripting Reference](https://jrsoftware.org/ishelp/topic_scriptfunctions.htm)
- [StringChangeEx Documentation](https://jrsoftware.org/ishelp/topic_isxfunc_stringchangeex.htm)

## Best Practices

1. **Always call feedback before committing**
2. **Make minimal changes** - don't rewrite working code
3. **Use double quotes** for YAML string values
4. **Test string handling carefully** - Pascal is different from other languages
5. **Understand procedure vs function** differences
6. **Check CI builds immediately** after pushing changes
7. **Reference official documentation** for unfamiliar functions
8. **Keep changes focused** - one logical change per commit

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

  // 3. Build Matrix section with double-quoted values
  config := 'matrix:' + #13#10 +
            '  homeserver: "' + HomeserverURL + '"' + #13#10 +
            '  bot_user_id: "' + bot_user_id + '"' + #13#10;

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

#### For YAML Double-Quoted Values (Recommended)

```pascal
// No escaping needed - safest approach
config := config + '  field: "' + rawValue + '"' + #13#10;
```

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
            '    store_path: "' + InstallDir + '\e2ee_store"' + #13#10;
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

- Always use double quotes for string values
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
