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
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline

[Files]
Source: "..\dist\mmrelay.exe"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; AfterInstall: AfterInstall(ExpandConstant('{app}'));

[Icons]
Name: "{group}\MM Relay"; Filename: "{app}\mmrelay.bat"; Check: FileExists(ExpandConstant('{app}\mmrelay.bat'))
Name: "{group}\MM Relay Config"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\config.yaml"""; WorkingDir: "{app}"; Check: FileExists(ExpandConstant('{app}\config.yaml'))
Name: "{group}\Setup Authentication"; Filename: "{app}\setup-auth.bat"; Comment: "Set up Matrix authentication for MM Relay"; Check: FileExists(ExpandConstant('{app}\setup-auth.bat'))
Name: "{group}\Logout"; Filename: "{app}\logout.bat"; Comment: "Logout and clear credentials"; Check: FileExists(ExpandConstant('{app}\logout.bat'))

[Run]
Filename: "{app}\mmrelay.bat"; Description: "Launch MM Relay"; Flags: nowait postinstall skipifsilent unchecked; Check: FileExists(ExpandConstant('{app}\mmrelay.bat'))

[Code]



var
  OverwriteConfig: TInputOptionWizardPage;
  MatrixMeshtasticPage: TInputQueryWizardPage;
  MeshtasticPage: TInputQueryWizardPage;
  OptionsPage: TInputOptionWizardPage;




procedure InitializeWizard;
begin
  OverwriteConfig := CreateInputOptionPage(wpWelcome,
    'Configure the relay', 'Create new configuration',
    '', False, False);
  MeshtasticPage := CreateInputQueryPage(OverwriteConfig.ID,
      'Meshtastic Setup', 'Configure Meshtastic Settings',
      'Enter the settings for connecting with your Meshtastic radio.');
  MatrixMeshtasticPage := CreateInputQueryPage(MeshtasticPage.ID,
      'Matrix <> Meshtastic Setup', 'Configure Matrix <> Meshtastic Settings',
      'Connect a Matrix room with a Meshtastic radio channel.');
  OptionsPage := CreateInputOptionPage(MatrixMeshtasticPage.ID,
      'Additional Options', 'Provide additional options',
      'Set logging and broadcast options, you can keep the defaults.', False, False);

  // Increase page height
  WizardForm.ClientHeight := WizardForm.ClientHeight + 50;

  OverwriteConfig.Add('Generate configuration (overwrite any current config files)');
  OverwriteConfig.Values[0] := False;

  MeshtasticPage.Add('Connection type (tcp, serial, ble):', False);
  MeshtasticPage.Add('Serial port (if serial):', False);
  MeshtasticPage.Add('Hostname/IP (if tcp):', False);
  MeshtasticPage.Add('BLE address/name (if ble):', False);
  MeshtasticPage.Add('Meshnet name:', False);

  MeshtasticPage.Edits[0].Hint := 'tcp (recommended), serial, ble';
  MeshtasticPage.Edits[1].Hint := 'serial port (if serial)';
  MeshtasticPage.Edits[2].Hint := 'hostname/IP (if tcp)';
  MeshtasticPage.Edits[3].Hint := 'BLE address or name (if ble)';
  MeshtasticPage.Edits[4].Hint := 'Name for radio Meshnet';

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

// Validate inputs as user navigates the wizard.
// Keeps AfterInstall validations as a safety net.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  connection_type: string;
  serial_port: string;
  host: string;
  ble_address: string;
  chan: string;
  chanInt: Integer;
begin
  Result := True;

  // Only validate when generating configuration
  if not OverwriteConfig.Values[0] then
    Exit;

  // Validate Meshtastic connection page
  if CurPageID = MeshtasticPage.ID then
  begin
    connection_type := LowerCase(Trim(MeshtasticPage.Values[0]));
    if connection_type = 'network' then
      connection_type := 'tcp';

    if (connection_type <> 'tcp') and (connection_type <> 'serial') and (connection_type <> 'ble') then
    begin
      MsgBox('Connection type must be tcp, serial, or ble.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    serial_port := Trim(MeshtasticPage.Values[1]);
    host := Trim(MeshtasticPage.Values[2]);
    ble_address := Trim(MeshtasticPage.Values[3]);

    if (connection_type = 'serial') and (serial_port = '') then
    begin
      MsgBox('Serial selected but no serial port provided.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if (connection_type = 'tcp') and (host = '') then
    begin
      MsgBox('TCP selected but no hostname/IP provided.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if (connection_type = 'ble') and (ble_address = '') then
    begin
      MsgBox('BLE selected but no BLE address/name provided.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  // Validate Matrix <> Meshtastic page
  if CurPageID = MatrixMeshtasticPage.ID then
  begin
    if Trim(MatrixMeshtasticPage.Values[0]) = '' then
    begin
      MsgBox('Please enter a Matrix room ID or alias.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    chan := Trim(MatrixMeshtasticPage.Values[1]);
    if chan <> '' then
    begin
      chanInt := StrToIntDef(chan, -1);
      if (chanInt < 0) or (chanInt > 7) or (IntToStr(chanInt) <> chan) then
      begin
        MsgBox('Invalid Meshtastic channel. Enter a number 0–7.', mbError, MB_OK);
        Result := False;
        Exit;
      end;
    end;
  end;
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
  setup_auth_batch: string;
  logout_batch: string;
  SafeRoomId: string;
  SafeSerial: string;
  SafeHost: string;
  SafeBle: string;
  SafeMesh: string;
  tempPath: string;
  meshtastic_channel: string;
  chanInt: Integer;
begin
  // Always create batch files regardless of config generation setting
  batch_file := '@echo off' + #13#10 +
                'cd /d "' + sAppDir + '"' + #13#10 +
                'echo Starting MM Relay...' + #13#10 +
                'echo.' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" --data-dir "' + sAppDir + '" --config "' + sAppDir + '\config.yaml"' + #13#10 +
                'echo.' + #13#10 +
                'echo MM Relay has stopped.' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\mmrelay.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "mmrelay.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
  end;

  // Create setup-auth.bat for manual authentication
  setup_auth_batch := '@echo off' + #13#10 +
                      'echo Setting up Matrix authentication for MM Relay...' + #13#10 +
                      'echo.' + #13#10 +
                      'cd /d "' + sAppDir + '"' + #13#10 +
                      '"' + sAppDir + '\mmrelay.exe" --data-dir "' + sAppDir + '" --config "' + sAppDir + '\config.yaml" auth login' + #13#10 +
                      'echo.' + #13#10 +
                      'echo Authentication setup complete.' + #13#10 +
                      'pause';

  if Not SaveStringToFile(sAppDir + '\setup-auth.bat', setup_auth_batch, false) then
  begin
    MsgBox('Could not create setup batch file "setup-auth.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
  end;

  // Create logout.bat for manual logout
  logout_batch := '@echo off' + #13#10 +
                  'echo Logging out and clearing all session data...' + #13#10 +
                  'echo.' + #13#10 +
                  'cd /d "' + sAppDir + '"' + #13#10 +
                  '"' + sAppDir + '\mmrelay.exe" --data-dir "' + sAppDir + '" --config "' + sAppDir + '\config.yaml" auth logout' + #13#10 +
                  'echo.' + #13#10 +
                  'echo Logout complete.' + #13#10 +
                  'pause';

  if Not SaveStringToFile(sAppDir + '\logout.bat', logout_batch, false) then
  begin
    MsgBox('Could not create logout batch file "logout.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
  end;

  // Only generate config.yaml if user checked the option
  If Not OverwriteConfig.Values[0] then
    Exit;

  if FileExists(sAppDir + '\config.yaml') then
  begin
    if FileExists(sAppDir + '\config-old.yaml') then
      DeleteFile(sAppDir + '\config-old.yaml');
    if not RenameFile(sAppDir + '\config.yaml', sAppDir + '\config-old.yaml') then
    begin
      MsgBox('Could not rename existing "config.yaml". Installation will continue but existing config may be overwritten.', mbInformation, MB_OK);
    end;
  end;

  connection_type := LowerCase(Trim(MeshtasticPage.Values[0]));
  if connection_type = 'network' then
  begin
    connection_type := 'tcp';
  end;
  serial_port := Trim(MeshtasticPage.Values[1]);
  host := Trim(MeshtasticPage.Values[2]);
  ble_address := Trim(MeshtasticPage.Values[3]);

  if OptionsPage.Values[0] then
  begin
    log_level := 'debug';
  end
  else
  begin
    log_level := 'info';
  end;

  // Default/validate Meshtastic channel
  meshtastic_channel := Trim(MatrixMeshtasticPage.Values[1]);
  if meshtastic_channel = '' then
    meshtastic_channel := '0'; // default to primary channel
  // Ensure integer 0–7 (use StrToIntDef to avoid exceptions)
  chanInt := StrToIntDef(meshtastic_channel, -1);
  if (chanInt < 0) or (chanInt > 7) or (IntToStr(chanInt) <> meshtastic_channel) then
  begin
    MsgBox('Invalid Meshtastic channel. Enter a number 0–7.', mbError, MB_OK);
    Abort;
  end;
  meshtastic_channel := IntToStr(chanInt);

  SafeRoomId := MatrixMeshtasticPage.Values[0];
  StringChangeEx(SafeRoomId, '''', '''''', True);
  config := 'matrix_rooms:' + #13#10 +
            '  - id: ''' + SafeRoomId + '''' + #13#10 +
            '    meshtastic_channel: ' + meshtastic_channel + #13#10 +
            'meshtastic:' + #13#10 +
            '  connection_type: "' + connection_type + '"' + #13#10;

  if connection_type = 'serial' then
  begin
    if Trim(serial_port) = '' then
    begin
      MsgBox('Serial selected but no serial port provided. Using default COM3.', mbInformation, MB_OK);
      serial_port := 'COM3';
    end;
    // Use single quotes to avoid backslash-escape pitfalls; escape internal single quotes
    SafeSerial := serial_port; StringChangeEx(SafeSerial, '''', '''''', True);
    config := config + '  serial_port: ''' + SafeSerial + '''' + #13#10
  end
  else if (connection_type = 'tcp') then
  begin
    if Trim(host) = '' then
    begin
      MsgBox('TCP selected but no hostname/IP provided. Using default localhost.', mbInformation, MB_OK);
      host := 'localhost';
    end;
    // Use single quotes for host; escape internal single quotes
    SafeHost := host; StringChangeEx(SafeHost, '''', '''''', True);
    config := config + '  host: ''' + SafeHost + '''' + #13#10
  end
  else if connection_type = 'ble' then
  begin
    if Trim(ble_address) = '' then
    begin
      MsgBox('BLE selected but no BLE address/name provided. Using placeholder.', mbInformation, MB_OK);
      ble_address := 'YOUR_BLE_ADDRESS';
    end;
    // Use single quotes for BLE address; escape internal single quotes
    SafeBle := ble_address; StringChangeEx(SafeBle, '''', '''''', True);
    config := config + '  ble_address: ''' + SafeBle + '''' + #13#10;
  end;

  // Use single quotes for meshnet name; escape internal single quotes
  SafeMesh := MeshtasticPage.Values[4]; StringChangeEx(SafeMesh, '''', '''''', True);
  config := config + '  meshnet_name: ''' + SafeMesh + '''' + #13#10 +
            '  broadcast_enabled: ' + BoolToStr(OptionsPage.Values[1]) + #13#10 +
            'logging:' + #13#10 +
            '  level: "' + log_level + '"' + #13#10 +
            'plugins:' + #13#10;

  tempPath := sAppDir + '\config.new.yaml';
  if not SaveStringToFile(tempPath, config, False) then
  begin
    MsgBox('Could not create temporary config file. Trying direct write.', mbInformation, MB_OK);
    // Try direct write as fallback
    if not SaveStringToFile(sAppDir + '\config.yaml', config, False) then
    begin
      MsgBox('Could not create config file. Installation will continue but you may need to create config manually.', mbInformation, MB_OK);
    end;
  end
  else
  begin
    if not RenameFile(tempPath, sAppDir + '\config.yaml') then
    begin
      MsgBox('Could not finalize config write. Trying direct write.', mbInformation, MB_OK);
      DeleteFile(tempPath);
      // Try direct write as fallback
      if not SaveStringToFile(sAppDir + '\config.yaml', config, False) then
      begin
        MsgBox('Could not create config file. Installation will continue but you may need to create config manually.', mbInformation, MB_OK);
      end;
    end;
  end;

  // Show completion message with setup instructions (always show regardless of config generation)
  MsgBox('MM Relay installation complete!' + #13#10 + #13#10 +
         'Batch files created: mmrelay.bat, setup-auth.bat, logout.bat' + #13#10 + #13#10 +
         'Next step: Run "setup-auth.bat" to configure Matrix authentication.' + #13#10 +
         'Then run "mmrelay.bat" to start the relay.' + #13#10 + #13#10 +
         'Files are located in: ' + sAppDir, mbInformation, MB_OK);
end;
