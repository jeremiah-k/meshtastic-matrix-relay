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
  ConnectionTypeCombo: TNewComboBox;
  MatrixPage: TInputQueryWizardPage;
  MatrixMeshtasticPage: TInputQueryWizardPage;
  MeshtasticPage: TInputQueryWizardPage;
  OptionsPage: TInputOptionWizardPage;
  Connection: string;

function ExtractHostFromURL(const Url: string): string;
var S: string; P, i, colonCount, lastColonPos, rb: Integer;
begin
  S := Trim(Url);
  P := Pos('://', S); if P > 0 then S := Copy(S, P + 3, MaxInt);
  P := Pos('/', S);   if P > 0 then S := Copy(S, 1, P - 1);
  // Handle [IPv6]:port by stripping brackets
  if (Length(S) > 0) and (S[1] = '[') then
  begin
    rb := Pos(']', S);
    if rb > 1 then
      S := Copy(S, 2, rb - 2);
  end
  else
  begin
    // Only strip :port when there is exactly one ':' and no brackets (avoid unbracketed IPv6)
    colonCount := 0; lastColonPos := 0;
    for i := 1 to Length(S) do
    begin
      if S[i] = ':' then
      begin
        colonCount := colonCount + 1;
        lastColonPos := i;
      end;
    end;
    if colonCount = 1 then
      S := Copy(S, 1, lastColonPos - 1);
  end;
  Result := S;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  // Only validate when generating configuration
  if not OverwriteConfig.Values[0] then
    Exit;

  // Validate Matrix page
  if CurPageID = MatrixPage.ID then
  begin
    if Trim(MatrixPage.Values[0]) = '' then
    begin
      MsgBox('Please enter your Matrix homeserver URL.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Trim(MatrixPage.Values[1]) = '' then
    begin
      MsgBox('Please enter your Matrix username.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  // Validate Meshtastic page
  if CurPageID = MeshtasticPage.ID then
  begin
    // Simplified validation for dropdown
    if ConnectionTypeCombo.ItemIndex = 1 and Trim(MeshtasticPage.Values[1]) = '' then
    begin
      MsgBox('Serial connection selected, but no serial port provided.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if ConnectionTypeCombo.ItemIndex = 0 and Trim(MeshtasticPage.Values[2]) = '' then
    begin
      MsgBox('Network connection selected, but no hostname/IP provided.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if ConnectionTypeCombo.ItemIndex = 2 and Trim(MeshtasticPage.Values[3]) = '' then
    begin
      MsgBox('BLE connection selected, but no BLE address provided.', mbError, MB_OK);
      Result := False;
      Exit;
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
  bot_user_id: string;
  HomeserverURL: string;
  ServerName: string;
begin
  If Not OverwriteConfig.Values[0] then
    Exit;

  if (FileExists(sAppDir + '/config.yaml')) then
  begin
    if not RenameFile(sAppDir + '/config.yaml', sAppDir + '/config-old.yaml') then
    begin
        MsgBox('Failed to back up existing config.yaml. Please ensure the file is not open in another application and run the installer again.', mbError, MB_OK);
        Abort;
    end;
  end;

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
  
  // Process Matrix homeserver and username
  HomeserverURL := Trim(MatrixPage.Values[0]);
  if Pos('://', HomeserverURL) = 0 then
    HomeserverURL := 'https://' + HomeserverURL;

  // Extract host from URL (strip scheme and any path)
  ServerName := ExtractHostFromURL(HomeserverURL);

  // If IPv6 literal, ensure brackets for MXID server name
  if (Length(ServerName) > 0) and (Pos(':', ServerName) > 0) then
  begin
    if not ((ServerName[1] = '[') and (ServerName[Length(ServerName)] = ']')) then
      ServerName := '[' + ServerName + ']';
  end;

  // Build bot_user_id (accept full MXID only when it looks like one)
  bot_user_id := Trim(MatrixPage.Values[1]);
  if (Length(bot_user_id) > 0) and (bot_user_id[1] = '@') and (Pos(':', bot_user_id) > 1) then
  begin
    bot_user_id := Trim(bot_user_id); // use as-is
  end
  else
  begin
    if (Length(bot_user_id) > 0) and (bot_user_id[1] = '@') then
      bot_user_id := Copy(bot_user_id, 2, MaxInt);
    bot_user_id := Trim(bot_user_id);
    if bot_user_id = '' then
    begin
      MsgBox('Invalid username. Enter a username (without "@") or a full MXID like @name:server.', mbError, MB_OK);
      Abort;
    end;
    bot_user_id := '@' + bot_user_id + ':' + ServerName;
  end;

  matrix_homeserver := HomeserverURL;
  matrix_username := bot_user_id;
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
            '  bot_user_id: "' + matrix_username + '"' + #13#10 +
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

  if Not SaveStringToFile(sAppDir + '/config.yaml', config, false) then
  begin
    MsgBox('Could not create config file "config.yaml". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  batch_file := '@echo off' + #13#10 +
                'cd /d "' + sAppDir + '"' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" --config "' + sAppDir + '\config.yaml" ' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '/mmrelay.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "mmrelay.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  // Create setup-auth.bat for easy authentication setup
  batch_file := '@echo off' + #13#10 +
                'echo Setting up Matrix authentication...' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" auth login --data-dir "' + sAppDir + '"' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\setup-auth.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "setup-auth.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;

  // Create logout.bat for easy authentication cleanup
  batch_file := '@echo off' + #13#10 +
                'echo Logging out from Matrix authentication...' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" auth logout --data-dir "' + sAppDir + '"' + #13#10 +
                'pause';

  if Not SaveStringToFile(sAppDir + '\logout.bat', batch_file, false) then
  begin
    MsgBox('Could not create batch file "logout.bat". Close any applications that may have it open and re-run setup', mbInformation, MB_OK);
  end;
end;