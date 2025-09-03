# MMRelay Android

This directory contains the Android application for MMRelay, providing a mobile interface for the Meshtastic-Matrix relay functionality.

## Features

- **Native Android UI**: Easy-to-use interface for configuration and control
- **Background Service**: Runs MMRelay continuously in the background
- **Bluetooth Support**: Direct connection to Meshtastic devices via Bluetooth
- **E2E Encryption**: Full support for Matrix end-to-end encryption
- **Foreground Service**: Ensures the relay stays running with user notification
- **Auto-start**: Can automatically start after device reboot

## Building

### Prerequisites

- Android Studio (optional, for local development)
- Docker (for CI builds)
- GitHub Actions (for automated builds)

### CI Build (Recommended)

The project includes a GitHub Actions workflow that builds the APK using a pre-configured Docker image:

1. Push changes to the `feature/android-scaffolding` branch
2. The workflow will automatically build the APK
3. Download the APK from the workflow artifacts

### Local Build

```bash
cd android
./gradlew assembleDebug
```

The APK will be available at: `android/app/build/outputs/apk/debug/app-debug.apk`

## Installation

1. Enable "Unknown Sources" in Android settings (for debug builds)
2. Transfer the APK to your Android device
3. Install the APK
4. Grant the requested permissions when prompted

## Usage

### Initial Setup

1. **Launch the App**: Open MMRelay on your Android device
2. **Configure Matrix**: Enter your Matrix homeserver, user ID, and password/access token
3. **Configure Meshtastic**: Select connection type (Serial, TCP, or Bluetooth) and enter device details
4. **Save Configuration**: Tap "Save Config" to store your settings

### Starting the Relay

1. **Start Service**: Tap the "Start MMRelay" button
2. **Grant Permissions**: Allow Bluetooth and location permissions if prompted
3. **Monitor Status**: The app will show "RUNNING" when the service is active
4. **Background Operation**: The relay will continue running even when the app is closed

### Stopping the Relay

- Tap the "Stop MMRelay" button to stop the service
- The service can also be stopped from the notification

## Configuration

### Matrix Settings

- **Homeserver**: Your Matrix server URL (e.g., `https://matrix.org`)
- **User ID**: Your Matrix user ID (e.g., `@user:matrix.org`)
- **Password**: Your Matrix password (for initial login)
- **Access Token**: Matrix access token (alternative to password)

### Meshtastic Settings

- **Connection Type**:
  - **Serial**: Direct USB/serial connection
  - **TCP**: Network connection to Meshtastic device
  - **Bluetooth**: Wireless connection (recommended for mobile)

- **Device**: Serial port path (for serial connections)
- **Host**: IP address and port (for TCP connections)

## Permissions

The app requires the following permissions:

- **Internet**: For Matrix communication
- **Bluetooth**: For Meshtastic device connection
- **Location**: For Bluetooth device discovery
- **Storage**: For configuration and log files
- **Foreground Service**: For background operation

## Architecture

### Components

- **MainActivity**: Main user interface
- **ConfigurationActivity**: Settings and configuration
- **RelayService**: Background service running MMRelay
- **AndroidConfigManager**: Android-specific configuration handling
- **BootReceiver**: Auto-start functionality

### Python Integration

The app uses Chaquopy to embed the Python MMRelay codebase:

- All Python dependencies are bundled in the APK
- Python code runs in a separate process from the Android UI
- Android-specific paths and logging are configured automatically

## Troubleshooting

### Service Won't Start

1. Check that all permissions are granted
2. Verify configuration is correct
3. Check device logs for error messages
4. Ensure Meshtastic device is available and powered on

### Bluetooth Connection Issues

1. Enable Bluetooth on your Android device
2. Ensure location services are enabled
3. Check that the Meshtastic device is in pairing mode
4. Verify the device address is correct

### Matrix Connection Issues

1. Verify homeserver URL is correct
2. Check user credentials
3. Ensure internet connection is available
4. Check Matrix server status

## Development

### Project Structure

```
android/
├── app/
│   ├── src/main/
│   │   ├── java/com/example/mmrelay/  # Kotlin source code
│   │   ├── python/mmrelay/            # Python MMRelay code
│   │   ├── res/layout/                # UI layouts
│   │   └── AndroidManifest.xml        # App manifest
│   └── build.gradle                   # App configuration
├── build.gradle                       # Project configuration
├── settings.gradle                    # Project settings
└── gradlew                            # Gradle wrapper
```

### Adding New Features

1. **Kotlin Code**: Add new activities/services in `app/src/main/java/com/example/mmrelay/`
2. **UI Layouts**: Create XML layouts in `app/src/main/res/layout/`
3. **Python Integration**: Modify Python code in `app/src/main/python/mmrelay/`
4. **Dependencies**: Add to `app/build.gradle` for Android libraries

### Testing

- Unit tests: `app/src/test/java/`
- Integration tests: `app/src/androidTest/java/`
- Python tests: Run via Chaquopy integration

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test on Android device
5. Submit a pull request

## License

This project is licensed under the GPL-3.0-or-later license.
