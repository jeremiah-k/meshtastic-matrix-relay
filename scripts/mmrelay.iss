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
Name: "{group}\MM Relay"; Filename: "{app}\mmrelay.bat"; Check: FileExists(ExpandConstant('{app}\mmrelay.bat'))
Name: "{group}\MM Relay Config"; Filename: "{app}\config.yaml"; IconFilename: "{sys}\notepad.exe"; WorkingDir: "{app}"; Parameters: "config.yaml"; Check: FileExists(ExpandConstant('{app}\config.yaml'))
Name: "{group}\Setup Authentication"; Filename: "{app}\setup-auth.bat"; Comment: "Set up Matrix authentication for MM Relay"; Check: FileExists(ExpandConstant('{app}\setup-auth.bat'))

[Run]
Filename: "{app}\setup-auth.bat"; Description: "Set up Matrix authentication (recommended first step)"; Flags: nowait postinstall skipifsilent; Check: FileExists(ExpandConstant('{app}\setup-auth.bat'))
Filename: "{app}\mmrelay.bat"; Description: "Launch MM Relay"; Flags: nowait postinstall skipifsilent unchecked; Check: FileExists(ExpandConstant('{app}\mmrelay.bat'))

[Code]
var
  TokenInfoLabel: TLabel;
  TokenInfoLink: TNewStaticText;
  MatrixPage: TInputQueryWizardPage;
  OverwriteConfig: TInputOptionWizardPage;
  MatrixMeshtasticPage: TInputQueryWizardPage;
  MeshtasticPage: TInputQueryWizardPage;
  OptionsPage: TInputOptionWizardPage;
  Connection: string;



procedure InitializeWizard;
begin
  OverwriteConfig := CreateInputOptionPage(wpWelcome,
    'Configure the relay', 'Create new configuration',
    '', False, False);
  MatrixPage := CreateInputQueryPage(OverwriteConfig.ID,
      'Matrix Setup', 'Configure Matrix Settings',
      'Enter the settings for your Matrix server.');
  MeshtasticPage := CreateInputQueryPage(MatrixPage.ID,
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

  MatrixPage.Add('Homeserver (example: https://matrix.org):', False);
  MatrixPage.Add('Bot username (example: mybotuser):', False);
  MatrixPage.Add('Password:', True);

  TokenInfoLabel := TLabel.Create(WizardForm);
  TokenInfoLabel.Caption := 'MMRelay will use modern authentication' + #13#10 + '(compatible with Matrix 2.0/MAS).';
  TokenInfoLabel.Parent := MatrixPage.Surface;
  TokenInfoLabel.Left := 0;
  TokenInfoLabel.Top := MatrixPage.Edits[2].Top + MatrixPage.Edits[2].Height + 8;
  TokenInfoLabel.WordWrap := True;
  TokenInfoLabel.Width := MatrixPage.Surface.Width;

  TokenInfoLink := TNewStaticText.Create(WizardForm);
  TokenInfoLink.Caption := 'No access tokens needed - secure OIDC authentication' + #13#10 + 'will be used automatically.';
  TokenInfoLink.Parent := MatrixPage.Surface;
  TokenInfoLink.Left := TokenInfoLabel.Left;
  TokenInfoLink.Top := TokenInfoLabel.Top + TokenInfoLabel.Height + 4;

  MatrixPage.Edits[0].Hint := 'https://example.matrix.org';
  MatrixPage.Edits[1].Hint := 'mybotuser (without @ or :server)';
  MatrixPage.Edits[2].Hint := 'Your Matrix account password';

  MeshtasticPage.Add('Connection type (tcp, serial, or ble):', False);
  MeshtasticPage.Add('Serial port (if serial):', False);
  MeshtasticPage.Add('Hostname/IP (if tcp/network):', False);
  MeshtasticPage.Add('BLE address/name (if ble):', False);
  MeshtasticPage.Add('Meshnet name:', False);

  MeshtasticPage.Edits[0].Hint := 'tcp (recommended), serial, or ble';
  MeshtasticPage.Edits[1].Hint := 'serial port (if serial)';
  MeshtasticPage.Edits[2].Hint := 'hostname/IP (if tcp/network)';
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
  HomeserverURL: string;
  ServerName: string;
  ProtocolPos: Integer;
  PathPos: Integer;
  bot_user_id: string;
  auth_command: string;
  auth_result: Integer;
  cfgPath: string;

  cfgLines: TArrayOfString;
  i: Integer;
  SafeHomeserver: string;
  SafeUser: string;
  SafePwd: string;
begin
  If Not OverwriteConfig.Values[0] then
    Exit;

  if (FileExists(sAppDir + '\config.yaml')) then
  begin
    RenameFile(sAppDir + '\config.yaml', sAppDir + '\config-old.yaml');
  end;

  connection_type := LowerCase(Trim(MeshtasticPage.Values[0]));
  if connection_type = 'network' then connection_type := 'tcp';
  serial_port := MeshtasticPage.Values[1];
  host := MeshtasticPage.Values[2];
  ble_address := MeshtasticPage.Values[3];

  if OptionsPage.Values[0] then
  begin
    log_level := 'debug';
  end
  else
  begin
    log_level := 'info';
  end;

  // Normalize homeserver (default https when scheme missing)
  HomeserverURL := Trim(MatrixPage.Values[0]);
  if Pos('://', HomeserverURL) = 0 then
    HomeserverURL := 'https://' + HomeserverURL;

  // Extract host from URL (strip scheme and any path)
  ServerName := HomeserverURL;
  ProtocolPos := Pos('://', ServerName);
  if ProtocolPos > 0 then
    ServerName := Copy(ServerName, ProtocolPos + 3, MaxInt);
  PathPos := Pos('/', ServerName);
  if PathPos > 0 then
    ServerName := Copy(ServerName, 1, PathPos - 1);
  // extra paranoia: strip trailing slashes
  while (Length(ServerName) > 0) and (ServerName[Length(ServerName)] = '/') do
    Delete(ServerName, Length(ServerName), 1);

  // Build bot_user_id (accept full MXID if provided)
  bot_user_id := Trim(MatrixPage.Values[1]);
  if (Pos(':', bot_user_id) > 0) then
  begin
    if (bot_user_id[1] <> '@') then
      bot_user_id := '@' + bot_user_id;
    bot_user_id := Trim(bot_user_id);
  end
  else
  begin
    if (Length(bot_user_id) > 0) and (bot_user_id[1] = '@') then
      bot_user_id := Copy(bot_user_id, 2, MaxInt);
    bot_user_id := '@' + Trim(bot_user_id) + ':' + ServerName;
  end;

  config := 'matrix:' + #13#10 +
            '  homeserver: "' + HomeserverURL + '"' + #13#10 +
            '  bot_user_id: "' + bot_user_id + '"' + #13#10;
  // append password line only when provided
  if MatrixPage.Values[2] <> '' then
    config := config + '  password: ''' + StringChange(MatrixPage.Values[2], '''', ''''''') + '''' + #13#10;
  config := config +
            'matrix_rooms:' + #13#10 +
            '  - id: "' + MatrixMeshtasticPage.Values[0] + '"' + #13#10 +
            '    meshtastic_channel: ' + MatrixMeshtasticPage.Values[1] + #13#10 +
            'meshtastic:' + #13#10 +
            '  connection_type: "' + connection_type + '"' + #13#10;

  if connection_type = 'serial' then
    config := config + '  serial_port: "' + serial_port + '"' + #13#10
  else if (connection_type = 'tcp') or (connection_type = 'network') then
    config := config + '  host: "' + host + '"' + #13#10
  else if connection_type = 'ble' then
    config := config + '  ble_address: "' + ble_address + '"' + #13#10;

  config := config + '  meshnet_name: "' + MeshtasticPage.Values[4] + '"' + #13#10 +
            '  broadcast_enabled: ' + BoolToStr(OptionsPage.Values[1]) + #13#10 +
            'logging:' + #13#10 +
            '  level: "' + log_level + '"' + #13#10 +
            'plugins:' + #13#10;

  if Not SaveStringToFile(sAppDir + '\config.yaml', config, false) then
  begin
    MsgBox('Could not create config file "config.yaml". Close any applications that may have it open and re-run setup', mbError, MB_OK);
  end;

  batch_file := '@echo off' + #13#10 +
                'cd /d "' + sAppDir + '"' + #13#10 +
                'echo Starting MM Relay...' + #13#10 +
                'echo.' + #13#10 +
                '"' + sAppDir + '\mmrelay.exe" --config "' + sAppDir + '\config.yaml"' + #13#10 +
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
                      '"' + sAppDir + '\mmrelay.exe" auth login' + #13#10 +
                      'echo.' + #13#10 +
                      'echo Authentication setup complete.' + #13#10 +
                      'pause';

  if Not SaveStringToFile(sAppDir + '\setup-auth.bat', setup_auth_batch, false) then
  begin
    MsgBox('Could not create setup batch file "setup-auth.bat". Close any applications that may have it open and re-run setup', mbError, MB_OK);
  end;

  // Set up Matrix authentication directly during installation
  if (HomeserverURL <> '') and (MatrixPage.Values[1] <> '') and (MatrixPage.Values[2] <> '') then
  begin
    // Run authentication command (auto-detects non-interactive mode when all params provided)
    // bot_user_id was already constructed earlier for config generation
    // Build params without invoking a shell
    // Escape embedded quotes for CLI (CreateProcess-compatible)
    SafeHomeserver := StringChange(HomeserverURL, '"', '""');
    SafeUser := StringChange(bot_user_id, '"', '""');
    SafePwd := StringChange(MatrixPage.Values[2], '"', '""');
    auth_command := 'auth login --homeserver "' + SafeHomeserver + '" --username "' + SafeUser + '" --password "' + SafePwd + '"';
      if Exec('"' + sAppDir + '\mmrelay.exe"', auth_command, sAppDir, SW_HIDE, ewWaitUntilTerminated, auth_result) then
    begin
      if auth_result = 0 then
      begin
        MsgBox('✅ Matrix authentication successful!' + #13#10 + 'MM Relay is ready to use.', mbInformation, MB_OK);
        // Scrub password from config.yaml after successful authentication
        cfgPath := sAppDir + '\config.yaml';
        if LoadStringsFromFile(cfgPath, cfgLines) then
        begin
          // Remove any line that contains password
          for i := 0 to GetArrayLength(cfgLines) - 1 do
          begin
            if Pos('password:', Trim(cfgLines[i])) = 1 then
            begin
              cfgLines[i] := '  # password removed after successful auth';
            end;
          end;
          SaveStringsToFile(cfgPath, cfgLines, False);
        end;
      end
      else
      begin
        MsgBox('❌ Matrix authentication failed.' + #13#10 + 'Please check your credentials and try running:' + #13#10 + 'mmrelay auth login', mbError, MB_OK);
      end;
    end
    else
    begin
      MsgBox('❌ Could not run authentication command.' + #13#10 + 'Please run manually: mmrelay auth login', mbError, MB_OK);
    end;
    end;
  end;
end;