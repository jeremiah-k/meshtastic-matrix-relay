[Setup]
// Add the custom wizard page to the installation
//WizardImageFile=wizard.bmp
//WizardSmallImageFile=smallwiz.bmp

AppName=Matrix <> Meshtastic Relay
AppVersion={#AppVersion}
DefaultDirName={userpf}\MM Relay
DefaultGroupName=MM Relay
UninstallFilesDir={app}
OutputDir=.
OutputBaseFilename=MMRelay_setup_{#AppVersion}
PrivilegesRequiredOverridesAllowed=dialog commandline

[Files]
Source: "..\dist\mmrelay.exe"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; AfterInstall: AfterInstall(ExpandConstant('{app}'));

[Icons]
Name: "{group}\MM Relay"; Filename: "{app}\mmrelay.bat"
Name: "{group}\MM Relay Config"; Filename: "{app}\config.yaml"; IconFilename: "{sys}\notepad.exe"; WorkingDir: "{app}"; Parameters: "config.yaml";

[Run]
Filename: "{app}\mmrelay.bat"; Description: "Launch MM Relay"; Flags: nowait postinstall

[Code]
var
  OverwriteConfig: TInputOptionWizardPage;
  MatrixPage: TInputQueryWizardPage;
  MatrixMeshtasticPage: TInputQueryWizardPage;
  MeshtasticPage: TInputQueryWizardPage;
  OptionsPage: TInputOptionWizardPage;
  Connection: string;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // Check directory permissions before starting file installation
    if not CheckDirectoryPermissions(ExpandConstant('{app}')) then
    begin
      Abort;
    end;
  end;
end;

procedure InitializeWizard;
begin
  OverwriteConfig := CreateInputOptionPage(wpWelcome,
    'Configure the relay', 'Create new configuration',
    '', False, False);
  MeshtasticPage := CreateInputQueryPage(OverwriteConfig.ID,
      'Meshtastic Setup', 'Configure Meshtastic Settings',
      'Enter the settings for connecting with your Meshtastic radio.');
  MatrixPage := CreateInputQueryPage(MeshtasticPage.ID,
      'Matrix Setup', 'Configure Matrix Authentication',
      'Enter your Matrix server and account details for authentication.');
  MatrixMeshtasticPage := CreateInputQueryPage(MatrixPage.ID,
      'Matrix <> Meshtastic Setup', 'Configure Matrix <> Meshtastic Settings',
      'Connect a Matrix room with a Meshtastic radio channel.');
  OptionsPage := CreateInputOptionPage(MatrixMeshtasticPage.ID,
      'Additional Options', 'Provide additional options',
      'Set logging and broadcast options, you can keep the defaults.', False, False);

  // Increase page height
  WizardForm.ClientHeight := WizardForm.ClientHeight + 50;

  OverwriteConfig.Add('Generate configuration (overwrite any current config files)');
  OverwriteConfig.Values[0] := False;

  MeshtasticPage.Add('Connection type (network, serial, or ble):', False);
  MeshtasticPage.Add('Serial port (if serial):', False);
  MeshtasticPage.Add('Hostname/IP (if network):', False);
  MeshtasticPage.Add('BLE address/name (if ble):', False);
  MeshtasticPage.Add('Meshnet name:', False);

  MeshtasticPage.Edits[0].Hint := 'network, serial, or ble';
  MeshtasticPage.Edits[1].Hint := 'serial port (if serial)';
  MeshtasticPage.Edits[2].Hint := 'hostname/IP (if network)';
  MeshtasticPage.Edits[3].Hint := 'BLE address or name (if ble)';
  MeshtasticPage.Edits[4].Hint := 'Name for radio Meshnet';

  MatrixPage.Add('Matrix homeserver URL (e.g., https://matrix.org):', False);
  MatrixPage.Add('Matrix username (without @):', False);
  MatrixPage.Add('Matrix password:', True);
  MatrixPage.Edits[0].Hint := 'https://matrix.org or https://your.server.com';
  MatrixPage.Edits[1].Hint := 'yourusername';
  MatrixPage.Edits[2].Hint := 'Your Matrix account password';

  MatrixMeshtasticPage.Add('Matrix room ID/alias (example: #someroom:example.matrix.org):', False);
  MatrixMeshtasticPage.Add('Meshtastic channel # (0 is primary, 1-7 secondary):', False);
  MatrixMeshtasticPage.Edits[0].Hint := '!someroomid:example.matrix.org';
  MatrixMeshtasticPage.Edits[1].Hint := '0-7 (default 0)';

  OptionsPage.Add('Detailed logging');
  OptionsPage.Add('Radio broadcasts enabled');
  OptionsPage.Values[0] := True;
  OptionsPage.Values[1] := True;
end;

function BoolToStr(Value: Boolean): String;
begin
  if Value then
    result := 'true'
  else
    result := 'false';
end;



{ Skips config setup pages if needed}
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  if PageID = OverwriteConfig.ID then
    Result := False
  else
    Result := Not OverwriteConfig.Values[0];
end;

function ValidateInput(): Boolean;
begin
  Result := True;
  
  // Validate Meshtastic connection type
  if (MeshtasticPage.Values[0] <> 'network') and 
     (MeshtasticPage.Values[0] <> 'serial') and 
     (MeshtasticPage.Values[0] <> 'ble') then
  begin
    MsgBox('Invalid connection type. Please enter "network", "serial", or "ble".', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  // Validate required fields based on connection type
  if MeshtasticPage.Values[0] = 'serial' then
  begin
    if Trim(MeshtasticPage.Values[1]) = '' then
    begin
      MsgBox('Serial port is required when connection type is "serial".', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  
  if MeshtasticPage.Values[0] = 'network' then
  begin
    if Trim(MeshtasticPage.Values[2]) = '' then
    begin
      MsgBox('Hostname/IP is required when connection type is "network".', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  
  if MeshtasticPage.Values[0] = 'ble' then
  begin
    if Trim(MeshtasticPage.Values[3]) = '' then
    begin
      MsgBox('BLE address/name is required when connection type is "ble".', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  
  // Validate Matrix settings
  if Trim(MatrixPage.Values[0]) = '' then
  begin
    MsgBox('Matrix homeserver URL is required.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  if Trim(MatrixPage.Values[1]) = '' then
  begin
    MsgBox('Matrix username is required.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  if Trim(MatrixPage.Values[2]) = '' then
  begin
    MsgBox('Matrix password is required.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  // Validate Matrix room
  if Trim(MatrixMeshtasticPage.Values[0]) = '' then
  begin
    MsgBox('Matrix room ID/alias is required.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  // Validate Meshtastic channel
  try
    if (StrToInt(MatrixMeshtasticPage.Values[1]) < 0) or (StrToInt(MatrixMeshtasticPage.Values[1]) > 7) then
    begin
      MsgBox('Meshtastic channel must be between 0 and 7.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  except
    begin
      MsgBox('Invalid Meshtastic channel number. Please enter a number between 0 and 7.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  
  // Validate meshnet name
  if Trim(MeshtasticPage.Values[4]) = '' then
  begin
    MsgBox('Meshnet name is required.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
end;

function SafePathCombine(BasePath, FileName: string): string;
begin
  // Ensure base path ends with backslash
  if Copy(BasePath, Length(BasePath), 1) <> '\' then
    BasePath := BasePath + '\';
  
  Result := BasePath + FileName;
end;

function CheckDirectoryPermissions(DirPath: string): Boolean;
var
  TestFile: string;
begin
  Result := True;
  
  // Check if directory exists
  if not DirExists(DirPath) then
  begin
    MsgBox('Installation directory does not exist: ' + DirPath, mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  // Test write permissions by creating a temporary file
  TestFile := SafePathCombine(DirPath, 'test_write.tmp');
  try
    if not SaveStringToFile(TestFile, 'test', false) then
    begin
      MsgBox('Cannot write to installation directory. Please check permissions.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    
    // Clean up test file
    DeleteFile(TestFile);
  except
    begin
      MsgBox('Error testing write permissions: ' + GetExceptionMessage, mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

function GetExceptionMessage: string;
begin
  // This is a simplified exception message handler
  // In a real scenario, you might want more detailed exception handling
  Result := 'An unexpected error occurred during installation.';
end;

procedure AfterInstall(sAppDir: string);
var
  config: string;
  connection_type: string;
  serial_port: string;
  host: string;
  ble_address: string;
  log_level: string;
  batch_file: string;
  matrix_homeserver: string;
  matrix_username: string;
  matrix_password: string;
  config_path: string;
  backup_path: string;
  batch_path: string;
  auth_batch_path: string;
  logout_batch_path: string;
begin
  If Not OverwriteConfig.Values[0] then
    Exit;

  // Validate user input before proceeding
  if not ValidateInput() then
  begin
    MsgBox('Please correct the errors in the configuration and try again.', mbError, MB_OK);
    Abort;
  end;

  // Use safe path construction
  config_path := SafePathCombine(sAppDir, 'config.yaml');
  backup_path := SafePathCombine(sAppDir, 'config-old.yaml');
  batch_path := SafePathCombine(sAppDir, 'mmrelay.bat');
  auth_batch_path := SafePathCombine(sAppDir, 'setup-auth.bat');
  logout_batch_path := SafePathCombine(sAppDir, 'logout.bat');

  if (FileExists(config_path)) then
  begin
    if not RenameFile(config_path, backup_path) then
    begin
        MsgBox('Failed to back up existing config.yaml. Please ensure the file is not open in another application and run the installer again.', mbError, MB_OK);
        Abort;
    end;
  end;

  connection_type := MeshtasticPage.Values[0];
  serial_port := MeshtasticPage.Values[1];
  host := MeshtasticPage.Values[2];
  ble_address := MeshtasticPage.Values[3];
  matrix_homeserver := MatrixPage.Values[0];
  matrix_username := MatrixPage.Values[1];
  matrix_password := MatrixPage.Values[2];

  if OptionsPage.Values[0] then
  begin
    log_level := 'debug';
  end
  else
  begin
    log_level := 'info';
  end;

  config := 'matrix:' + #13#10 +
            '  homeserver: "' + matrix_homeserver + '"' + #13#10 +
            '  bot_user_id: "@' + matrix_username + '"' + #13#10 +
            '  password: "' + matrix_password + '"' + #13#10 +
            'matrix_rooms:' + #13#10 +
            '  - id: "' + MatrixMeshtasticPage.Values[0] + '"' + #13#10 +
            '    meshtastic_channel: ' + MatrixMeshtasticPage.Values[1] + #13#10 +
            'meshtastic:' + #13#10 +
            '  connection_type: "' + connection_type + '"' + #13#10;

  if connection_type = 'serial' then
    config := config + '  serial_port: "' + serial_port + '"' + #13#10
  else if connection_type = 'network' then
    config := config + '  host: "' + host + '"' + #13#10
  else if connection_type = 'ble' then
    config := config + '  ble_address: "' + ble_address + '"' + #13#10;

  config := config + '  meshnet_name: "' + MeshtasticPage.Values[4] + '"' + #13#10 +
            '  broadcast_enabled: ' + BoolToStr(OptionsPage.Values[1]) + #13#10 +
            'logging:' + #13#10 +
            '  level: "' + log_level + '"' + #13#10 +
            'plugins:' + #13#10;

  // Create config file with error handling
  if Not SaveStringToFile(config_path, config, false) then
  begin
    MsgBox('Could not create config file "config.yaml". Close any applications that may have it open and re-run setup', mbError, MB_OK);
    Abort;
  end;

  // Verify config file was created successfully
  if not FileExists(config_path) then
  begin
    MsgBox('Config file was not created successfully. Please check permissions and try again.', mbError, MB_OK);
    Abort;
  end;

  // Create main batch file
  batch_file := '@echo off' + #13#10 +
                'echo Starting MM Relay...' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" --config "' + config_path + '" ' + #13#10 +
                'echo MM Relay has stopped. Press any key to close...' + #13#10 +
                'pause';

  if Not SaveStringToFile(batch_path, batch_file, false) then
  begin
    MsgBox('Could not create batch file "mmrelay.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
    Abort;
  end;

  // Create setup-auth.bat for easy authentication setup
  batch_file := '@echo off' + #13#10 +
                'echo Setting up Matrix authentication...' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" auth login --data-dir "' + sAppDir + '"' + #13#10 +
                'echo Authentication setup complete. Press any key to close...' + #13#10 +
                'pause';

  if Not SaveStringToFile(auth_batch_path, batch_file, false) then
  begin
    MsgBox('Could not create batch file "setup-auth.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
    Abort;
  end;

  // Create logout.bat for easy authentication cleanup
  batch_file := '@echo off' + #13#10 +
                'echo Logging out from Matrix authentication...' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" auth logout --data-dir "' + sAppDir + '"' + #13#10 +
                'echo Logout complete. Press any key to close...' + #13#10 +
                'pause';

  if Not SaveStringToFile(logout_batch_path, batch_file, false) then
  begin
    MsgBox('Could not create batch file "logout.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
    Abort;
  end;

  // Success message
  MsgBox('Configuration files created successfully! You can now run MM Relay using the desktop shortcut.', mbInformation, MB_OK);
end;