# Custom plugins

Custom plugins allow users to create their own behaviors when a Meshtastic or Matrix message is detected. A sample plugin `hello_world.py` is provided.

NOTE: Custom plugins are not supported with the Windows installer option.

## Development

Custom plugins should be written as a subclass of `plugins.base_plugin.BasePlugin` and given a file extension `.py`. The class should be given a unique `plugin_name`.

## Installation

Custom plugins should be copied to `custom_plugins` directory. They are detected upon startup of the relay.

## Troubleshooting

Each plugin has access to a `self.logger` that can be useful in troubleshooting runtime issues.
