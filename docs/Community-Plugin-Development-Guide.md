> ⚠️ **Important Note for Existing Plugin Authors**: If you are updating an old plugin, check your `__init__()` method order. The order of operations is critical - you must set `self.plugin_name` **before** calling `super().__init__()`. See [Plugin Name Initialization: A Critical Detail](#plugin-name-initialization-a-critical-detail) for details.

Welcome to the M<>M Relay plugin development guide! This document will get you started with writing plugins for the relay system. It covers setting up a development environment, understanding the `BasePlugin` class, and creating your first plugin. This guide is here to help you extend the relay's functionality with custom plugins tailored to your specific needs.

## Changes in v1.0

With the release of v1.0, there are some important changes to plugin development:

- **New Plugin Locations**: Plugins are now stored in standardized locations:
  - Core plugins: Included in the package
  - Custom plugins: `~/.mmrelay/plugins/custom/`
  - Community plugins: `~/.mmrelay/plugins/community/`

- **Standardized Data Storage**: Plugin data should be stored in standardized locations:
  - Database data: Use `BasePlugin` methods like `store_node_data()` and `get_node_data()`
  - File data: Use `self.get_plugin_data_dir()` to get `~/.mmrelay/data/plugins/<plugin_name>/`

- **Absolute Imports**: The codebase now uses absolute imports. When developing plugins, you should use:
  ```python
  from mmrelay.plugins.base_plugin import BasePlugin
  from mmrelay.meshtastic_utils import connect_meshtastic
  from mmrelay.matrix_utils import bot_command, matrix_client
  ```

- **Global Matrix Client**: Always use the global `matrix_client` instance from `matrix_utils` or the `send_matrix_message()` method from `BasePlugin`. Never call `connect_matrix()` directly in your plugins.

- **Configuration Path**: Configuration is now stored at `~/.mmrelay/config.yaml` by default

- **Separate Branch and Tag Parameters**: Community plugins now support separate `branch:` and `tag:` parameters in config.yaml, allowing for more precise version control.

- **Automatic Dependency Installation**: The plugin system now automatically detects and installs dependencies from requirements.txt files, with support for both pip and pipx installations.

- **Improved Error Handling**: Better error messages and logging for plugin loading issues, with specific guidance for resolving dependency problems.

## Prerequisites

To develop plugins for M<>M Relay, you'll need:

- Python 3.9+
- A working installation of the M<>M Relay application
- Familiarity with Python and some experience with asynchronous programming (if you're new to these, this is a good way to learn!)
- A text editor or IDE (e.g., VS Code, PyCharm. I prefer [VSCodium](https://vscodium.com/))

## Understanding the Plugin System

Plugins in M<>M Relay are Python classes that extend what the relay can do. All plugins inherit from a shared base class called `BasePlugin`. This class provides essential methods and utilities for message handling, logging, and data persistence. By using `BasePlugin` as your starting point, you can create plugins that interact with either the Meshtastic meshnet, Matrix rooms, or both.

### Structure of the Base Plugin

The `BasePlugin` is designed to provide a consistent interface for all plugins. Here's a quick look at some of its important features:

> ⚠️ **Critical Note**: When creating a plugin, you must set `self.plugin_name` **before** calling `super().__init__()` in your `__init__` method. See [Plugin Name Initialization: A Critical Detail](#plugin-name-initialization-a-critical-detail) for details.

-   **Logging**: Each plugin has its own logger (`self.logger`) to help with tracking actions and debugging.
-   **Data Storage**: Methods like `store_node_data()`, `get_node_data()`, and `delete_node_data()` let plugins persistently store data specific to nodes.
-   **Message Handling**: Plugins can react to incoming messages from Meshtastic or Matrix by implementing specific methods.
-   **Configuration Options**: Plugins can access configuration options like `channels` from the `config.yaml` file. Note that the `plugin_response_delay` is now configured globally under the `meshtastic` section.

Each plugin must implement two key methods:

-   `handle_meshtastic_message(packet, formatted_message, longname, meshnet_name)`
-   `handle_room_message(room, event, full_message)`

These functions define how your plugin will handle incoming messages from Meshtastic nodes and Matrix rooms, respectively.

## Minimalist Plugin Example

Let's start with a simple example to get your feet wet. This plugin will respond to a specific command without handling channels or DMs, making it easier to understand the basics.

### Step 1: Set Up Your Plugin Repository

Plugins should be in their own project repositories. Create a new project repository on GitHub, GitLab, Codeberg, etc. Clone your new repo to your local machine and open it in your editor. Inside your cloned project, create a new file for the plugin.

_Note: For an easy start, you can fork this template repository [mmr-plugin-template](https://github.com/jeremiah-k/mmr-plugin-template) and add your own code._

For this example, create a file named `simple_responder.py` in your project repository.

### Step 2: Create the Plugin Class

Every plugin must inherit from `BasePlugin` and set its unique `plugin_name`. Here's the complete code for the `simple_responder` plugin:

```python
from mmrelay.plugins.base_plugin import BasePlugin
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.matrix_utils import bot_command

class Plugin(BasePlugin):
    plugin_name = "simple_responder"

    def __init__(self):
        # IMPORTANT: Set plugin_name BEFORE calling super().__init__()
        # See "Plugin Name Initialization: A Critical Detail" section for explanation
        self.plugin_name = "simple_responder"
        super().__init__()

    async def handle_meshtastic_message(self, packet, formatted_message, longname, meshnet_name):
        if "decoded" in packet and "text" in packet["decoded"]:
            message = packet["decoded"]["text"].strip()

            if message == "!hello":
                meshtastic_client = connect_meshtastic()

                # Respond with a greeting
                meshtastic_client.sendText(text="Hello from Meshtastic!", channelIndex=0)
                return True  # Indicate that we handled the message
        return False  # Indicate that we did not handle the message

    async def handle_room_message(self, room, event, full_message):
        if bot_command("hello", event):
            # Use the send_matrix_message method from BasePlugin
            # This uses the global matrix_client internally
            await self.send_matrix_message(room.room_id, "Hello from Matrix!")
            return True  # Indicate that we handled the message
        return False  # Indicate that we did not handle the message
```

This example avoids complexities of channel and DM handling. It uses the `bot_command` function from `matrix_utils.py` to check if the message is a command directed at the bot, and `connect_meshtastic` from `meshtastic_utils.py` to send a message to the mesh network.

Notice that we define `plugin_name` twice: once as a class variable and once in the `__init__` method. This is important because the instance methods (like `handle_meshtastic_message`) use `self.plugin_name`, which needs to be initialized in `__init__`. Without this initialization, commands that use `self.plugin_name` in f-strings (like `f"!{self.plugin_name}"`) won't work correctly.

### Step 3: Activate the Plugin in Your Configuration

If your plugin is hosted in a repository and you want the relay to clone it automatically, specify the repository and either a branch or tag:

> **Note**: The plugin system now supports separate `branch:` and `tag:` parameters. Use `branch:` for development branches and `tag:` for specific version tags. If both are specified, the tag will take precedence.

```yaml
community-plugins:
  simple_responder:
    active: true
    repository: https://github.com/YourUsername/SimpleResponder.git
    branch: main  # Use branch: for branches
    # OR
    # tag: v1.0.0  # Use tag: for specific version tags
```

### Step 4: Adding Dependencies

If your plugin requires additional Python packages, you can include a `requirements.txt` file in your repository. The plugin loader will automatically detect and install these dependencies when the plugin is loaded.

```
# Example requirements.txt
gpxpy==1.5.0
requests==2.28.1
```

The plugin system will automatically detect whether mmrelay is installed with pip or pipx and use the appropriate method to install dependencies:
- For pip installations: `pip install -r requirements.txt`
- For pipx installations: `pipx inject mmrelay <package>` for each package

### Step 5: Running and Testing the Plugin

After adding the plugin to your configuration, restart the relay. You should see a line in your log indicating that the **simple_responder** plugin has started:

```bash
DEBUG:Plugin:simple_responder:Started with priority=10
```

You can then send messages via Meshtastic or Matrix to verify that the plugin is working as expected.

## Creating a "Hello World" Plugin

Now, let's create a **HelloWorld** plugin. This plugin will simply log "Hello world" when it receives a message from either Meshtastic or Matrix, but won't send any responses.

Create a file named `hello_world.py` and add the following code:

```python
from mmrelay.plugins.base_plugin import BasePlugin

class Plugin(BasePlugin):
    plugin_name = "hello_world"

    def __init__(self):
        # IMPORTANT: Set plugin_name BEFORE calling super().__init__()
        # See "Plugin Name Initialization: A Critical Detail" section for explanation
        self.plugin_name = "hello_world"
        super().__init__()

    async def handle_meshtastic_message(self, packet, formatted_message, longname, meshnet_name):
        self.logger.debug("Hello world, Meshtastic")
        return False  # Indicate that we did not handle the message

    async def handle_room_message(self, room, event, full_message):
        self.logger.debug("Hello world, Matrix")
        return False  # Indicate that we did not handle the message
```

Activate this plugin in your `config.yaml` just like you did with the `simple_responder` plugin.

## Plugin Name Initialization: A Critical Detail

You might have noticed that in both examples, we define `plugin_name` twice: once as a class variable and once in the `__init__` method. This isn't redundant—it's actually crucial for proper plugin operation.

### Initialization Order Matters

The order of operations in your plugin's `__init__` method is critical:

1. **FIRST**: Set `self.plugin_name`
2. **THEN**: Call `super().__init__()`

This is because `BasePlugin.__init__()` relies on `self.plugin_name` being set before it runs. If you call `super().__init__()` first, and then assign `self.plugin_name` afterward, the parent constructor runs without knowledge of the plugin's name, which causes failures in command recognition and plugin registration.

### Why This Is Important

When your plugin uses `self.plugin_name` in instance methods (like checking for commands with `f"!{self.plugin_name}"` in message text), it needs to be properly initialized in the `__init__` method. Without this initialization, commands won't be recognized correctly.

For example, in the weather plugin, this check:
```python
if f"!{self.plugin_name}" not in message.lower():
    return False
```

Would fail silently if `self.plugin_name` wasn't initialized in `__init__` before calling `super().__init__()`, causing the plugin to ignore valid commands.

### The Correct Pattern

Always follow this pattern in your plugins:
```python
class Plugin(BasePlugin):
    plugin_name = "your_plugin_name"  # Class variable for compatibility

    def __init__(self):
        self.plugin_name = "your_plugin_name"  # Must match the class variable
        super().__init__()  # Call parent constructor AFTER setting plugin_name
```

This ensures your plugin will correctly process commands and work as expected. The class-level attribute defines a default for the class, but `BasePlugin.__init__()` operates at the instance level and reads `self.plugin_name`. Setting it inside `__init__()` before calling `super().__init__()` ensures correct behavior at the instance level.

## Useful Functions from Other Modules

Besides the methods in `BasePlugin`, there are several functions in the relay's codebase you can use to avoid reinventing the wheel:

-   **Matrix Utilities**:
    -   `bot_command(command, event)`: A function from `matrix_utils.py` that helps determine if a Matrix message is a command directed at the bot.
    -   `matrix_client`: The global Matrix client instance. Always use this instead of calling `connect_matrix()` to avoid reinitializing the client.

     **Example Usage**:

    ```python
    from mmrelay.matrix_utils import bot_command, matrix_client

    async def handle_room_message(self, room, event, full_message):
        if bot_command("status", event):
            # Handle the 'status' command
            await self.send_matrix_message(room.room_id, "System is running smoothly.")
            return True  # Indicate that we handled the message
        return False
    ```

    > ⚠️ **Important Note**: Never call `connect_matrix()` directly in your plugins. Always use the global `matrix_client` instance or the `send_matrix_message()` method provided by `BasePlugin`. Calling `connect_matrix()` will reinitialize the Matrix client and cause unnecessary credential reloading.

-   **Meshtastic Utilities**:
    -   `connect_meshtastic()`: A function from `meshtastic_utils.py` that returns the Meshtastic client interface. Useful for sending messages or accessing node information.

    ```python
    from mmrelay.meshtastic_utils import connect_meshtastic

    meshtastic_client = connect_meshtastic()
    ```

-   **Database Utilities**:
    -   For storing plugin-specific data, use the data persistence methods provided by `BasePlugin`. If you need to access node information like long names or short names, you can use functions from `db_utils.py`.

        -   `get_longname(meshtastic_id)`: Retrieve the longname for a given Meshtastic ID.
        -   `get_shortname(meshtastic_id)`: Retrieve the shortname for a given Meshtastic ID.

    ```python
    from mmrelay.db_utils import get_longname, get_shortname

    longname = get_longname(sender_id)
    shortname = get_shortname(sender_id)
    ```

## Advanced Topics

As you get more comfortable with plugin development, you can explore advanced features to enhance your plugins.

### Handling Direct Messages (DMs) and Channel-Specific Responses

Plugins can respond differently to DMs and messages in specific channels. This is controlled by the `channels` setting in your plugin's configuration.

#### Responding Only to Direct Messages (DMs)

To make your plugin respond **only** to DMs and ignore all channel messages, set the `channels` list to be empty:

```yaml
plugins:
  my_plugin:
    active: true
    channels: []  # Empty list: plugin only responds to DMs
```

In your plugin, check whether to respond based on the channel and whether it's a DM using `is_channel_enabled`:

```python
if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
    self.logger.debug(f"Channel {channel} not enabled for plugin '{self.plugin_name}'")
    return False
```

#### Responding to All Mapped Channels

To make your plugin respond to all mapped channels (defined in `matrix_rooms`), either omit the `channels` setting or comment it out:

```yaml
plugins:
  my_plugin:
    active: true
    # channels: [0,1,3,4]  # Commented out or omitted; plugin responds to all mapped channels
```

By default, if `channels` is not specified, the plugin responds to all mapped channels. The `is_channel_enabled` method handles this logic internally.

#### Responding to Specific Channels

To make your plugin respond only to specific channels, list them in the `channels` setting:

```yaml
plugins:
  my_plugin:
    active: true
    channels: [0, 1, 3, 4]  # List of Meshtastic channels to respond to
```

#### Handling Direct Messages (DMs)

By default, plugins always respond to DMs if active, regardless of `channels` configuration. The `is_channel_enabled` method in `BasePlugin` ensures DMs are always enabled:

```python
def is_channel_enabled(self, channel, is_direct_message=False):
    if is_direct_message:
        return True  # Always respond to DMs if the plugin is active
    else:
        return channel in self.channels
```

When processing a message, determine if it's a DM and pass `is_direct_message`:

```python
# Determine if the message is a direct message
toId = packet.get("to")
myId = meshtastic_client.myInfo.my_node_num  # Relay's own node number

if toId == myId:
    is_direct_message = True
else:
    is_direct_message = False

if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
    return False
```

This ensures your plugin responds appropriately to DMs and channel messages based on your configuration.

### Using Data Persistence

MMRelay provides two main methods for plugins to store data:

1. **Database Storage**: For structured data that needs to be queried or updated frequently
2. **File System Storage**: For larger files, binary data, or data that needs to be accessed by external tools

#### Database Storage

`BasePlugin` provides easy-to-use methods for saving and retrieving plugin-specific data in the SQLite database:

```python
# Store data for a specific node
self.store_node_data(meshtastic_id, node_data)

# Set data for a specific node (replaces existing data)
self.set_node_data(meshtastic_id, node_data)

# Delete data for a specific node
self.delete_node_data(meshtastic_id)

# Get data for a specific node
node_data = self.get_node_data(meshtastic_id)

# Get all data for this plugin
all_data = self.get_data()
```

This is ideal for storing configuration settings, user preferences, or small amounts of structured data.

#### File System Storage

For larger files or binary data, MMRelay provides a standardized directory structure:

```
~/.mmrelay/data/plugins/<plugin_name>/
```

The `BasePlugin` class provides a method to get this directory:

```python
# Get the plugin's data directory
data_dir = self.get_plugin_data_dir()

# Get a subdirectory within the plugin's data directory
# This will create the subdirectory if it doesn't exist
subdir = self.get_plugin_data_dir('my_subdirectory')
```

##### Example: Storing Files

```python
import os
from mmrelay.plugins.base_plugin import BasePlugin

class Plugin(BasePlugin):
    plugin_name = "my_plugin"

    def __init__(self):
        self.plugin_name = "my_plugin"
        super().__init__()

        # Get the plugin's data directory
        self.data_dir = self.get_plugin_data_dir()

        # Or get a specific subdirectory
        self.images_dir = self.get_plugin_data_dir('images')

    def save_file(self, filename, content):
        file_path = os.path.join(self.data_dir, filename)
        with open(file_path, 'wb') as f:
            f.write(content)
```

##### Custom Data Directories

If your plugin needs to store data in a custom location (like a user-specified directory outside of `~/.mmrelay`), you can support this through configuration:

```python
# Check for custom directory in config
custom_dir = self.config.get('data_directory')
if custom_dir:
    # Expand user directory if needed (e.g., ~/my_data -> /home/user/my_data)
    custom_dir = os.path.expanduser(custom_dir)
    self.logger.info(f"Using custom data directory from config: {custom_dir}")
    self.data_dir = custom_dir
    # Ensure the directory exists
    os.makedirs(self.data_dir, exist_ok=True)
else:
    # Use the standardized plugin data directory
    self.data_dir = self.get_plugin_data_dir()
```

This allows users to specify their preferred data location in the plugin's configuration:

```yaml
plugins:
  my_plugin:
    active: true
    data_directory: ~/my_plugin_data
```

### Scheduling Background Tasks

If your plugin needs to perform periodic tasks, use the built-in scheduling capabilities from `BasePlugin`. The `start()` method lets you set up recurring background jobs:

```python
def start(self):
    schedule.every(5).minutes.do(self.background_job)
```

### Handling Meshtastic-Specific Commands

You can create specific commands for handling messages from the Meshtastic network. Modify `handle_meshtastic_message()` to parse commands from Meshtastic nodes.

**Example Using Existing Functions**:

```python
from mmrelay.meshtastic_utils import connect_meshtastic

async def handle_meshtastic_message(self, packet, formatted_message, longname, meshnet_name):
    if "decoded" in packet and "text" in packet["decoded"]:
        message = packet["decoded"]["text"].strip()
        channel = packet.get("channel", 0)

        meshtastic_client = connect_meshtastic()

        # Determine if the message is a direct message
        toId = packet.get("to")
        myId = meshtastic_client.myInfo.my_node_num  # Relay's own node number

        if toId == myId:
            is_direct_message = True
        else:
            is_direct_message = False

        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
            return False

        if message == "!ping":
            # Wait for the response delay
            await asyncio.sleep(self.get_response_delay())

            fromId = packet.get("fromId")

            if is_direct_message:
                # Respond via DM
                meshtastic_client.sendText(
                    text="pong",
                    destinationId=fromId,
                )
            else:
                # Respond in the same channel
                meshtastic_client.sendText(
                    text="pong",
                    channelIndex=channel,
                )
            return True  # Indicate that we handled the message
    return False  # Indicate that we did not handle the message
```

**Note on Response Delay**: If your plugin automatically responds to mesh commands, respect the `plugin_response_delay` configuration option. It's set globally under `meshtastic` in `config.yaml`. Retrieve it using `self.get_response_delay()` and apply it before sending your response, as shown above. This helps manage network traffic and prevents overwhelming the mesh network.

**Example `config.yaml` Setting:**

```yaml
meshtastic:
  plugin_response_delay: 5  # Delay in seconds before plugin responses; defaults to 3
```

## Appendix: `bot_command()` Implementation

The `bot_command` function is a helper utility located in `matrix_utils.py`. It's designed to figure out if an incoming Matrix message is a command directed at your bot. The reason the regex is so complicated is because it handles the various different ways Matrix clients format messages and mentions.

**Note: You don't need to modify this function.** It's already included in the `matrix_utils.py` file. You can simply import and use it in your plugins as shown in the examples.

For reference, here's the implementation of `bot_command()`:

```python
def bot_command(command, event):
    """
    Checks if the given command is directed at the bot,
    accounting for variations in different Matrix clients.
    """
    full_message = event.body.strip()
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    # Check if the message starts with bot_user_id or bot_user_name
    if full_message.startswith(bot_user_id) or text_content.startswith(bot_user_id):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}$"
        return bool(re.match(pattern, full_message)) or bool(re.match(pattern, text_content))
    elif full_message.startswith(bot_user_name) or text_content.startswith(bot_user_name):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}$"
        return bool(re.match(pattern, full_message)) or bool(re.match(pattern, text_content))
    else:
        return False
```

## Best Practices

1. **Use Logging Liberally**: Use `self.logger` to record key events, errors, or other relevant info for debugging and monitoring.
2. **Keep Plugins Modular**: Each plugin should handle a distinct piece of functionality for easier maintenance and debugging.
3. **Avoid Blocking Operations**: The relay is asynchronous, so be careful not to use blocking calls that could delay message handling.
4. **Respect Plugin Priorities**: Set plugin priorities appropriately by defining the `priority` attribute within your plugin class to control the order of message processing.
5. **Handle Response Delays**: If your plugin sends automatic responses, use `await asyncio.sleep(self.get_response_delay())` to respect the globally configured response delay.
6. **Manage Channel Responses**: Use `self.is_channel_enabled(channel, is_direct_message=is_direct_message)` to control where your plugin responds and ensure it handles DMs appropriately.
7. **Use Global Matrix Client**: Always use the global `matrix_client` from `matrix_utils` or the `send_matrix_message()` method from `BasePlugin`. Never call `connect_matrix()` directly, as this will reinitialize the client and cause unnecessary credential reloading.
8. **Leverage Existing Functions**: Utilize functions from `matrix_utils.py`, `meshtastic_utils.py`, and `db_utils.py` to simplify your plugin code and maintain consistency.
9. **Use Standardized Data Storage**: Store plugin data in the standardized locations:
   - For structured data: Use the database methods (`store_node_data()`, `get_node_data()`, etc.)
   - For files and binary data: Use `self.get_plugin_data_dir()` to get the plugin's data directory
10. **Initialize Plugin Name Properly**: Always initialize `self.plugin_name` in the `__init__` method **before** calling `super().__init__()`, even though it's also defined as a class variable. See [Plugin Name Initialization: A Critical Detail](#plugin-name-initialization-a-critical-detail) for a detailed explanation.

## Example Configuration

Here's a complete example of a community plugin configuration with all available options:

```yaml
community-plugins:
  gpxtracker:
    active: true
    repository: https://github.com/jeremiah-k/MMR-GPXTRacker.git
    # Use either tag or branch:
    tag: v1.0.0  # For a specific tag
    # OR
    branch: main  # For a specific branch
    # Optional custom data directory:
    gpx_directory: "~/my_gpx_files"  # Custom directory for GPX files
    # Optional plugin-specific configuration:
    allowed_device_ids:
      - "*"  # Allow all devices
    # Optional priority setting:
    priority: 50  # Default is 100, lower numbers run first
```

## Next Steps

Now that you know the basics, consider adding more complex features to your plugin. You could interact with external APIs, respond to specific commands, or handle more detailed data from the Meshtastic network. Check out existing plugins like `nodes_plugin.py` or `weather_plugin.py` for more ideas and examples.

If you have questions or run into issues, feel free to ask in the project's Matrix room [#mmrelay:meshnet.club](https://matrix.to/#/#mmrelay:meshnet.club).

Happy coding!
