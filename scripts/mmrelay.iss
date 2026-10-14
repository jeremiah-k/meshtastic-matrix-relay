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

[InstallDelete]
// Authentication shortcut name shipped before the MM Relay prefix was added.
Type: files; Name: "{group}\Setup Authentication.lnk"

[Icons]
Name: "{group}\MM Relay"; Filename: "{app}\mmrelay.bat"
Name: "{group}\MM Relay Config"; Filename: "{app}\mmrelay.exe"; IconFilename: "{sys}\notepad.exe"; WorkingDir: "{app}"; Parameters: "config edit";
Name: "{group}\MM Relay Migrate Data"; Filename: "{app}\migrate-data.bat"; WorkingDir: "{app}"
Name: "{group}\MM Relay Setup Authentication"; Filename: "{app}\setup-auth.bat"; WorkingDir: "{app}"
Name: "{group}\MM Relay OAuth Login"; Filename: "{app}\setup-auth.bat"; Parameters: "--oauth"; WorkingDir: "{app}"; Comment: "Experimental native Matrix OAuth browser approval (no password)"

[Run]
Filename: "{app}\migrate-data.bat"; Description: "Review and migrate existing MMRelay data (confirmation required)"; Flags: postinstall skipifsilent unchecked; WorkingDir: "{app}"; Check: ShouldOfferDataMigration
Filename: "{app}\mmrelay.bat"; Description: "Launch MM Relay"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
var
  OverwriteConfig: TInputOptionWizardPage;
  ConnectionTypeCombo: TNewComboBox;
  MatrixPage: TInputQueryWizardPage;
  MatrixMeshtasticPage: TInputQueryWizardPage;
  MeshtasticPage: TInputQueryWizardPage;
  OptionsPage: TInputOptionWizardPage;
  Connection: string;
  MigrationOfferChecked: Boolean;
  MigrationOfferAvailable: Boolean;
  MigrationOfferReady: Boolean;

function ShouldOfferDataMigration: Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if WizardSilent or not MigrationOfferReady then
    Exit;
  // Check functions can run repeatedly. Probe only the installed executable,
  // using the same user as post-install launchers, and cache its read-only result.
  if not FileExists(ExpandConstant('{app}\mmrelay.exe')) then
    Exit;
  if not MigrationOfferChecked then
  begin
    MigrationOfferChecked := True;
    if ExecAsOriginalUser(ExpandConstant('{app}\mmrelay.exe'),
      'migrate --to-default-home --check-needed', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      MigrationOfferAvailable := ResultCode = 0;
  end;
  Result := MigrationOfferAvailable;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Never run a probe against the executable being replaced during an upgrade.
  if CurStep = ssPostInstall then
    MigrationOfferReady := True;
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
      'Enter your Matrix server and account details for authentication.' + #13#10 + #13#10 + 'Note: Your password will be saved in plaintext in config.yaml. You can remove it after the first successful run when the bot has logged in.');
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

  MeshtasticPage.Add('Connection type:', False);
  MeshtasticPage.Add('Serial port (if serial):', False);
  MeshtasticPage.Add('Hostname/IP (if network):', False);
  MeshtasticPage.Add('BLE address/name (if ble):', False);
  MeshtasticPage.Add('Meshnet name:', False);

  MeshtasticPage.Edits[0].Hint := 'Select connection type from dropdown';
  MeshtasticPage.Edits[1].Hint := 'COM3, /dev/ttyUSB0, etc.';
  MeshtasticPage.Edits[2].Hint := '192.168.1.100, meshtastic.local, etc.';
  MeshtasticPage.Edits[3].Hint := 'BLE address or device name';
  MeshtasticPage.Edits[4].Hint := 'Name for radio Meshnet';

  // Create connection type dropdown to replace the first edit field
  ConnectionTypeCombo := TNewComboBox.Create(WizardForm);
  ConnectionTypeCombo.Parent := MeshtasticPage.Surface;
  ConnectionTypeCombo.Left := MeshtasticPage.Edits[0].Left;
  ConnectionTypeCombo.Top := MeshtasticPage.Edits[0].Top;
  ConnectionTypeCombo.Width := MeshtasticPage.Edits[0].Width;
  ConnectionTypeCombo.Style := csDropDownList;

  ConnectionTypeCombo.Items.Add('Network connection (TCP/IP)');
  ConnectionTypeCombo.Items.Add('Serial connection (USB/Serial)');
  ConnectionTypeCombo.Items.Add('Bluetooth Low Energy (BLE)');
  ConnectionTypeCombo.ItemIndex := 0; // Default to network

  // Hide the original edit field for connection type since we're replacing it with dropdown
  MeshtasticPage.Edits[0].Visible := False;

  MatrixPage.Add('Matrix homeserver URL (e.g., https://matrix.org):', False);
  MatrixPage.Add('Full Matrix user ID (e.g., @relay:example.org):', False);
  MatrixPage.Add('Matrix password:', True);
  MatrixPage.Edits[0].Hint := 'https://matrix.org or https://your.server.com';
  MatrixPage.Edits[1].Hint := '@relay:example.org';
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

function YamlString(Value: string): string;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
  StringChangeEx(Result, #13, '\r', True);
  StringChangeEx(Result, #10, '\n', True);
  Result := '"' + Result + '"';
end;

function YamlSecret(Value: string): string;
begin
  Result := Value;
  StringChangeEx(Result, '''', '''''', True);
  Result := '''' + Result + '''';
end;

function SaveUtf8Text(FileName, Value: string): Boolean;
var
  Lines: TArrayOfString;
begin
  SetArrayLength(Lines, 1);
  Lines[0] := Value;
  Result := SaveStringsToUTF8File(FileName, Lines, False);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  UserID: string;
  Channel: Integer;
begin
  Result := True;
  if not OverwriteConfig.Values[0] then
    Exit;
  if CurPageID = MatrixPage.ID then
  begin
    UserID := Trim(MatrixPage.Values[1]);
    if (Copy(UserID, 1, 1) <> '@') or (Pos(':', UserID) < 3) or (Pos(':', UserID) = Length(UserID)) then
    begin
      MsgBox('Enter the full Matrix user ID, for example @relay:example.org.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = MatrixMeshtasticPage.ID then
  begin
    Channel := StrToIntDef(Trim(MatrixMeshtasticPage.Values[1]), -1);
    if (Channel < 0) or (Channel > 7) then
    begin
      MsgBox('The Meshtastic channel must be a whole number from 0 to 7.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;



{ Skip only optional configuration pages; keep the standard Finish page. }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (Not OverwriteConfig.Values[0]) and
    ((PageID = MeshtasticPage.ID) or
     (PageID = MatrixPage.ID) or
     (PageID = MatrixMeshtasticPage.ID) or
     (PageID = OptionsPage.ID));
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
  data_home: string;
  config_path: string;
  staged_path: string;
  backup_path: string;
begin
  // Always update batch files to ensure they use current CLI flags
  // This fixes upgrades from versions that used deprecated --data-dir
  batch_file := '@echo off' + #13#10 +
                'cd /d "%~dp0"' + #13#10 +
                '"%~dp0mmrelay.exe" %*' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\mmrelay.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "mmrelay.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  // Create setup-auth.bat for easy authentication setup
  batch_file := '@echo off' + #13#10 +
                'cd /d "%~dp0"' + #13#10 +
                'echo Setting up Matrix authentication...' + #13#10 +
                '"%~dp0mmrelay.exe" auth login %*' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\setup-auth.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "setup-auth.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  // Create logout.bat for easy authentication cleanup
  batch_file := '@echo off' + #13#10 +
                'cd /d "%~dp0"' + #13#10 +
                'echo Logging out from Matrix authentication...' + #13#10 +
                '"%~dp0mmrelay.exe" auth logout %*' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\logout.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "logout.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  batch_file := '@echo off' + #13#10 +
                'cd /d "%~dp0"' + #13#10 +
                'echo Stop MMRelay and back up your data before continuing.' + #13#10 +
                '"%~dp0mmrelay.exe" migrate --to-default-home --dry-run' + #13#10 +
                'if errorlevel 1 goto done' + #13#10 +
                'choice /C YN /N /M "Move the data shown above? [Y/N] "' + #13#10 +
                'if errorlevel 2 goto done' + #13#10 +
                'if not errorlevel 1 goto done' + #13#10 +
                '"%~dp0mmrelay.exe" migrate --to-default-home' + #13#10 +
                ':done' + #13#10 +
                'pause';
  if Not SaveStringToFile(sAppDir + '\migrate-data.bat', batch_file, false) then
    MsgBox('Could not create migrate-data.bat. Run mmrelay migrate --to-default-home manually.', mbError, MB_OK);

  // Only generate config.yaml if user selected "Overwrite config"
  If Not OverwriteConfig.Values[0] then
    Exit;

  // New installs keep writable state outside the executable directory. Preserve
  // a single existing config location until the user explicitly migrates it.
  data_home := ExpandConstant('{localappdata}\mmrelay');
  if Not FileExists(data_home + '\config.yaml') then
  begin
    if FileExists(sAppDir + '\config.yaml') then
      data_home := sAppDir
    else if FileExists(ExpandConstant('{localappdata}\mmrelay\mmrelay\config.yaml')) then
      data_home := ExpandConstant('{localappdata}\mmrelay\mmrelay');
  end;
  if Not ForceDirectories(data_home) then
  begin
    MsgBox('Could not create the MMRelay data directory: ' + data_home, mbError, MB_OK);
    Exit;
  end;
  config_path := data_home + '\config.yaml';

  // Determine connection type from dropdown selection
  if ConnectionTypeCombo.ItemIndex = 0 then
    connection_type := 'network'
  else if ConnectionTypeCombo.ItemIndex = 1 then
    connection_type := 'serial'
  else if ConnectionTypeCombo.ItemIndex = 2 then
    connection_type := 'ble'
  else
    connection_type := 'network'; // fallback

  serial_port := MeshtasticPage.Values[1];
  host := MeshtasticPage.Values[2];
  ble_address := MeshtasticPage.Values[3];
  matrix_homeserver := MatrixPage.Values[0];
  matrix_username := Trim(MatrixPage.Values[1]);
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
            '  homeserver: ' + YamlString(matrix_homeserver) + #13#10 +
            '  bot_user_id: ' + YamlString(matrix_username) + #13#10;
  if matrix_password <> '' then
    config := config + '  password: ' + YamlSecret(matrix_password) + #13#10;
  config := config +
            'matrix_rooms:' + #13#10 +
            '  - id: ' + YamlString(Trim(MatrixMeshtasticPage.Values[0])) + #13#10 +
            '    meshtastic_channel: ' + IntToStr(StrToIntDef(Trim(MatrixMeshtasticPage.Values[1]), 0)) + #13#10 +
            'meshtastic:' + #13#10 +
            '  connection_type: "' + connection_type + '"' + #13#10;

  if connection_type = 'serial' then
    config := config + '  serial_port: ' + YamlString(serial_port) + #13#10
  else if connection_type = 'network' then
    config := config + '  host: ' + YamlString(host) + #13#10
  else if connection_type = 'ble' then
    config := config + '  ble_address: ' + YamlString(ble_address) + #13#10;

  config := config + '  meshnet_name: ' + YamlString(MeshtasticPage.Values[4]) + #13#10 +
            '  broadcast_enabled: ' + BoolToStr(OptionsPage.Values[1]) + #13#10 +
            'logging:' + #13#10 +
            '  level: "' + log_level + '"' + #13#10 +
            'plugins:' + #13#10;

  // Stage the complete UTF-8 document before touching an existing config.
  // Never leave the active configuration missing if writing or replacing fails.
  staged_path := config_path + '.new';
  if FileExists(staged_path) then
  begin
    MsgBox('A previous temporary config file exists: ' + staged_path + '. Review it before retrying.', mbError, MB_OK);
    Abort;
  end;
  if Not SaveUtf8Text(staged_path, config) then
  begin
    DeleteFile(staged_path);
    MsgBox('Could not write the new configuration. The existing config.yaml was not changed.', mbError, MB_OK);
    Abort;
  end;

  backup_path := '';
  if FileExists(config_path) then
  begin
    backup_path := config_path + '.' + GetDateTimeString('yyyymmddhhnnss', '-', ':') + '.bak';
    if not RenameFile(config_path, backup_path) then
    begin
      DeleteFile(staged_path);
      MsgBox('Could not back up the existing config.yaml. It was not changed.', mbError, MB_OK);
      Abort;
    end;
  end;

  if Not RenameFile(staged_path, config_path) then
  begin
    DeleteFile(staged_path);
    // Re-establish the original active configuration if replacement failed.
    if backup_path <> '' then
    begin
      if not RenameFile(backup_path, config_path) then
        MsgBox('Could not restore config.yaml. Its backup is at: ' + backup_path, mbError, MB_OK)
      else
        MsgBox('Could not install the new config.yaml. The previous configuration was restored.', mbError, MB_OK);
    end
    else
      MsgBox('Could not install the new config.yaml. No previous configuration existed.', mbError, MB_OK);
    Abort;
  end;
end;
