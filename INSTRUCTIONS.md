# Instructions

The relay works on Linux, macOS, and Windows and requires Python 3.9+.

## Installation

Clone the repository:

```bash
git clone https://github.com/geoffwhittington/meshtastic-matrix-relay.git
```

### Setup

1. Create a Python virtual environment in the project directory:

   ```bash
   python3 -m venv .pyenv
   ```

2. Activate the virtual environment:

   - Linux/macOS:
     ```bash
     source .pyenv/bin/activate
     ```
   - Windows:
     ```cmd
     .pyenv\Scripts\activate
     ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Configuration

Create a `config.yaml` in the project directory based on the `sample_config.yaml`.

## Usage

Run the relay with the virtual environment activated:

```bash
python main.py
```

Example output:

```bash
INFO:meshtastic.matrix.relay:Starting Meshtastic <==> Matrix Relay...
INFO:meshtastic.matrix.relay:Connecting to radio at meshtastic.local ...
INFO:meshtastic.matrix.relay:Connected to radio at meshtastic.local.
INFO:meshtastic.matrix.relay:Listening for inbound radio messages ...
INFO:meshtastic.matrix.relay:Listening for inbound matrix messages ...
INFO:meshtastic.matrix.relay:Processing matrix message from @bob:matrix.org: Hi Alice!
INFO:meshtastic.matrix.relay:Sending radio message from Bob to radio broadcast
INFO:meshtastic.matrix.relay:Processing inbound radio message from !613501e4 on channel 0
INFO:meshtastic.matrix.relay:Relaying Meshtastic message from Alice to Matrix: [Alice/VeryCoolMeshnet]: Hey Bob!
INFO:meshtastic.matrix.relay:Sent inbound radio message to matrix room: #someroomid:example.matrix.org
```

## Persistence (Linux)

To run the relay automatically on startup:

1. Copy the systemd service file:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp tools/mmrelay.service ~/.config/systemd/user/
   ```
2. Enable and start the service:
   ```bash
   systemctl --user enable mmrelay.service
   systemctl --user start mmrelay.service
   ```

## Dockerized Versions (Unofficial)

If you would prefer to use a Dockerized version of the relay, there are unofficial third-party projects available. Please note that these are not officially supported, and issues should be reported to their respective repositories. For more details, visit the [Third Party Projects](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Third-Party-Projects) page.

## Development

### Contributing

Contributions are welcome! We use **Trunk** for automated code quality checks and formatting. Please run Trunk checks before submitting pull requests.

#### Setting Up Trunk

1. Install Trunk:

   ```bash
   curl -fsSL https://get.trunk.io | bash
   ```

2. Initialize Trunk:

   ```bash
   trunk init
   ```

3. Run checks and fix issues:
   ```bash
   trunk check --all --fix
   ```

For more details, see the [Trunk documentation](https://trunk.io/docs).
