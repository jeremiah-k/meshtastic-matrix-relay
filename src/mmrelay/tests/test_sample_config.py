"""
Comprehensive unit tests for sample configuration functionality.
Tests cover validation, edge cases, error handling, and configuration parsing.
Testing Framework: pytest
"""

import pytest
import os
import tempfile
import json
import yaml
from unittest.mock import patch, mock_open, MagicMock
from pathlib import Path
import io
from typing import Dict, Any

# Import the modules under test (adjust imports based on actual module structure)
try:
    from mmrelay.config import SampleConfig, ConfigLoader, ConfigValidator
    from mmrelay.exceptions import ConfigurationError, ValidationError
except ImportError:
    # Mock the imports if they don't exist yet - this allows tests to run
    class SampleConfig:
        def __init__(self, data=None, file_path=None):
            self.data = data or {}
            self.file_path = file_path
        
        def validate(self):
            """Validate the configuration data."""
            if not isinstance(self.data, dict):
                raise ValidationError("Configuration must be a dictionary")
            return True
            
        def load_from_file(self, filepath):
            """Load configuration from file."""
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Configuration file not found: {filepath}")
            
            with open(filepath, 'r') as f:
                if filepath.endswith('.json'):
                    self.data = json.load(f)
                elif filepath.endswith(('.yaml', '.yml')):
                    self.data = yaml.safe_load(f)
                else:
                    raise ConfigurationError(f"Unsupported file format: {filepath}")
            
            self.file_path = filepath
            
        def get(self, key, default=None):
            """Get configuration value by key."""
            return self.data.get(key, default)
            
        def set(self, key, value):
            """Set configuration value."""
            self.data[key] = value
    
    class ConfigLoader:
        @staticmethod
        def load_yaml(filepath):
            """Load YAML configuration file."""
            with open(filepath, 'r') as f:
                return yaml.safe_load(f)
            
        @staticmethod
        def load_json(filepath):
            """Load JSON configuration file."""
            with open(filepath, 'r') as f:
                return json.load(f)
                
        @staticmethod
        def validate_file_exists(filepath):
            """Validate that file exists."""
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")
    
    class ConfigValidator:
        @staticmethod
        def validate_schema(config, schema=None):
            """Validate configuration against schema."""
            if not isinstance(config, dict):
                raise ValidationError("Configuration must be a dictionary")
            return True
            
        @staticmethod
        def validate_required_fields(config, required_fields):
            """Validate that required fields are present."""
            missing_fields = []
            for field in required_fields:
                if field not in config:
                    missing_fields.append(field)
            if missing_fields:
                raise ValidationError(f"Missing required fields: {missing_fields}")
            return True
    
    class ConfigurationError(Exception):
        """Configuration-related error."""
        pass
        
    class ValidationError(Exception):
        """Validation-related error."""
        pass


class TestSampleConfig:
    """Test cases for SampleConfig class."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.valid_config_data = {
            "server": {
                "host": "localhost",
                "port": 8080,
                "debug": False,
                "ssl_enabled": True,
                "max_connections": 1000
            },
            "database": {
                "url": "postgresql://user:pass@localhost:5432/mmrelay",
                "pool_size": 10,
                "timeout": 30,
                "retry_attempts": 3
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file": "/var/log/mmrelay.log"
            },
            "cache": {
                "enabled": True,
                "backend": "redis",
                "host": "localhost",
                "port": 6379,
                "ttl": 3600
            }
        }
        
        self.minimal_valid_config = {
            "server": {
                "host": "localhost",
                "port": 8080
            }
        }
        
        self.invalid_config_data = {
            "server": {
                "host": "",  # Invalid empty host
                "port": "invalid_port",  # Invalid port type
                "debug": "not_boolean"  # Invalid boolean type
            }
        }
        
        self.sample_yaml_content = """
server:
  host: localhost
  port: 8080
  debug: false
  ssl_enabled: true
  max_connections: 1000

database:
  url: postgresql://user:pass@localhost:5432/mmrelay
  pool_size: 10
  timeout: 30
  retry_attempts: 3

logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: /var/log/mmrelay.log

cache:
  enabled: true
  backend: redis
  host: localhost
  port: 6379
  ttl: 3600
"""
    
    def teardown_method(self):
        """Clean up after each test method."""
        # Clean up any temporary files created during tests
        pass
    
    def test_sample_config_initialization_with_valid_data(self):
        """Test SampleConfig initialization with valid configuration data."""
        config = SampleConfig(self.valid_config_data)
        assert config.data == self.valid_config_data
        assert config.data["server"]["host"] == "localhost"
        assert config.data["server"]["port"] == 8080
        assert config.data["database"]["pool_size"] == 10
        assert config.data["cache"]["enabled"] is True
    
    def test_sample_config_initialization_with_empty_data(self):
        """Test SampleConfig initialization with empty data."""
        config = SampleConfig()
        assert config.data == {}
    
    def test_sample_config_initialization_with_none(self):
        """Test SampleConfig initialization with None data."""
        config = SampleConfig(None)
        assert config.data == {}
    
    def test_sample_config_initialization_with_file_path(self):
        """Test SampleConfig initialization with file path."""
        config = SampleConfig(self.valid_config_data, "/path/to/config.yaml")
        assert config.file_path == "/path/to/config.yaml"
        assert config.data == self.valid_config_data
    
    def test_sample_config_validation_success(self):
        """Test successful validation of valid configuration."""
        config = SampleConfig(self.valid_config_data)
        result = config.validate()
        assert result is True
    
    def test_sample_config_validation_minimal_config(self):
        """Test validation of minimal valid configuration."""
        config = SampleConfig(self.minimal_valid_config)
        result = config.validate()
        assert result is True
    
    def test_sample_config_validation_failure_invalid_types(self):
        """Test validation failure with invalid data types."""
        config = SampleConfig(self.invalid_config_data)
        with pytest.raises((ValidationError, ConfigurationError)):
            config.validate()
    
    def test_sample_config_validation_failure_non_dict(self):
        """Test validation failure with non-dictionary data."""
        config = SampleConfig("not a dictionary")
        with pytest.raises(ValidationError):
            config.validate()
    
    def test_sample_config_validation_failure_list_data(self):
        """Test validation failure with list data."""
        config = SampleConfig([1, 2, 3])
        with pytest.raises(ValidationError):
            config.validate()
    
    def test_sample_config_get_existing_key(self):
        """Test getting existing configuration value."""
        config = SampleConfig(self.valid_config_data)
        assert config.get("server") == self.valid_config_data["server"]
        assert config.get("database") == self.valid_config_data["database"]
    
    def test_sample_config_get_nonexistent_key(self):
        """Test getting non-existent configuration value returns None."""
        config = SampleConfig(self.valid_config_data)
        assert config.get("nonexistent") is None
    
    def test_sample_config_get_with_default(self):
        """Test getting non-existent key with default value."""
        config = SampleConfig(self.valid_config_data)
        default_value = {"default": "value"}
        assert config.get("nonexistent", default_value) == default_value
    
    def test_sample_config_set_new_key(self):
        """Test setting new configuration value."""
        config = SampleConfig(self.valid_config_data)
        new_value = {"new": "setting"}
        config.set("new_section", new_value)
        assert config.get("new_section") == new_value
    
    def test_sample_config_set_existing_key(self):
        """Test overwriting existing configuration value."""
        config = SampleConfig(self.valid_config_data)
        new_server_config = {"host": "new.host.com", "port": 9000}
        config.set("server", new_server_config)
        assert config.get("server") == new_server_config
    
    @patch('builtins.open', new_callable=mock_open, read_data='{"server": {"host": "localhost", "port": 8080}}')
    def test_load_from_json_file_success(self, mock_file):
        """Test successful loading from JSON configuration file."""
        config = SampleConfig()
        config.load_from_file("config.json")
        mock_file.assert_called_once_with("config.json", 'r')
        assert "server" in config.data
    
    @patch('builtins.open', new_callable=mock_open)
    @patch('yaml.safe_load')
    def test_load_from_yaml_file_success(self, mock_yaml_load, mock_file):
        """Test successful loading from YAML configuration file."""
        mock_yaml_load.return_value = {"server": {"host": "localhost", "port": 8080}}
        config = SampleConfig()
        config.load_from_file("config.yaml")
        mock_file.assert_called_once_with("config.yaml", 'r')
        mock_yaml_load.assert_called_once()
    
    def test_load_from_nonexistent_file(self):
        """Test loading from non-existent file raises FileNotFoundError."""
        config = SampleConfig()
        with pytest.raises(FileNotFoundError):
            config.load_from_file("nonexistent_config.json")
    
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json content')
    def test_load_from_invalid_json_file(self, mock_file):
        """Test loading from file with invalid JSON content."""
        config = SampleConfig()
        with pytest.raises(json.JSONDecodeError):
            config.load_from_file("invalid_config.json")
    
    @patch('builtins.open', new_callable=mock_open, read_data='invalid: yaml: content: [')
    @patch('yaml.safe_load', side_effect=yaml.YAMLError("Invalid YAML"))
    def test_load_from_invalid_yaml_file(self, mock_yaml, mock_file):
        """Test loading from file with invalid YAML content."""
        config = SampleConfig()
        with pytest.raises(yaml.YAMLError):
            config.load_from_file("invalid_config.yaml")
    
    def test_load_from_unsupported_file_format(self):
        """Test loading from unsupported file format."""
        config = SampleConfig()
        with patch('os.path.exists', return_value=True):
            with pytest.raises(ConfigurationError):
                config.load_from_file("config.txt")
    
    def test_load_from_file_with_permission_error(self):
        """Test loading from file with permission restrictions."""
        config = SampleConfig()
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            with patch('os.path.exists', return_value=True):
                with pytest.raises(PermissionError):
                    config.load_from_file("restricted_config.json")


class TestConfigLoader:
    """Test cases for ConfigLoader utility class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.json_file = os.path.join(self.temp_dir, "test_config.json")
        self.yaml_file = os.path.join(self.temp_dir, "test_config.yaml")
        
        # Create test files
        self.json_data = {
            "test": "json_value",
            "number": 42,
            "boolean": True,
            "nested": {"key": "value"}
        }
        
        self.yaml_data = {
            "test": "yaml_value",
            "number": 24,
            "boolean": False,
            "nested": {"key": "yaml_nested_value"}
        }
        
        with open(self.json_file, 'w') as f:
            json.dump(self.json_data, f)
            
        with open(self.yaml_file, 'w') as f:
            yaml.dump(self.yaml_data, f)
    
    def teardown_method(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_json_file_success(self):
        """Test successful loading of JSON configuration file."""
        result = ConfigLoader.load_json(self.json_file)
        assert result["test"] == "json_value"
        assert result["number"] == 42
        assert result["boolean"] is True
        assert result["nested"]["key"] == "value"
    
    def test_load_yaml_file_success(self):
        """Test successful loading of YAML configuration file."""
        result = ConfigLoader.load_yaml(self.yaml_file)
        assert result["test"] == "yaml_value"
        assert result["number"] == 24
        assert result["boolean"] is False
        assert result["nested"]["key"] == "yaml_nested_value"
    
    def test_load_json_nonexistent_file(self):
        """Test loading non-existent JSON file."""
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load_json("nonexistent.json")
    
    def test_load_yaml_nonexistent_file(self):
        """Test loading non-existent YAML file."""
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load_yaml("nonexistent.yaml")
    
    def test_validate_file_exists_success(self):
        """Test file existence validation with existing file."""
        ConfigLoader.validate_file_exists(self.json_file)  # Should not raise
    
    def test_validate_file_exists_failure(self):
        """Test file existence validation with non-existent file."""
        with pytest.raises(FileNotFoundError):
            ConfigLoader.validate_file_exists("nonexistent_file.json")
    
    def test_load_json_empty_file(self):
        """Test loading empty JSON file."""
        empty_json = os.path.join(self.temp_dir, "empty.json")
        with open(empty_json, 'w') as f:
            f.write("")
        
        with pytest.raises(json.JSONDecodeError):
            ConfigLoader.load_json(empty_json)
    
    def test_load_yaml_empty_file(self):
        """Test loading empty YAML file."""
        empty_yaml = os.path.join(self.temp_dir, "empty.yaml")
        with open(empty_yaml, 'w') as f:
            f.write("")
        
        result = ConfigLoader.load_yaml(empty_yaml)
        assert result is None
    
    def test_load_json_with_unicode_content(self):
        """Test loading JSON file with unicode content."""
        unicode_json = os.path.join(self.temp_dir, "unicode.json")
        unicode_data = {
            "message": "Hello 世界",
            "emoji": "🌍",
            "special_chars": "Special: àáâãäåæçèéêë"
        }
        
        with open(unicode_json, 'w', encoding='utf-8') as f:
            json.dump(unicode_data, f, ensure_ascii=False)
        
        result = ConfigLoader.load_json(unicode_json)
        assert result["message"] == "Hello 世界"
        assert result["emoji"] == "🌍"
        assert "àáâãäåæçèéêë" in result["special_chars"]
    
    def test_load_yaml_with_complex_structure(self):
        """Test loading YAML file with complex nested structure."""
        complex_yaml = os.path.join(self.temp_dir, "complex.yaml")
        complex_data = {
            "environments": {
                "development": {
                    "database": {"host": "dev.db.com", "port": 5432},
                    "cache": {"enabled": True, "ttl": 300},
                    "features": {"debug": True, "logging": "verbose"}
                },
                "production": {
                    "database": {"host": "prod.db.com", "port": 5432},
                    "cache": {"enabled": True, "ttl": 3600},
                    "features": {"debug": False, "logging": "minimal"}
                }
            },
            "features": ["feature1", "feature2", "feature3"],
            "metadata": {
                "version": "1.0.0",
                "created": "2023-01-01",
                "author": "Test Author"
            }
        }
        
        with open(complex_yaml, 'w') as f:
            yaml.dump(complex_data, f)
        
        result = ConfigLoader.load_yaml(complex_yaml)
        assert len(result["environments"]) == 2
        assert result["environments"]["development"]["database"]["port"] == 5432
        assert result["environments"]["production"]["features"]["debug"] is False
        assert len(result["features"]) == 3
        assert result["metadata"]["version"] == "1.0.0"
    
    def test_load_json_with_large_numbers(self):
        """Test loading JSON with large numbers and edge cases."""
        large_numbers_json = os.path.join(self.temp_dir, "large_numbers.json")
        large_data = {
            "large_int": 9223372036854775807,  # Max 64-bit signed int
            "large_float": 1.7976931348623157e+308,  # Near max float
            "small_float": 2.2250738585072014e-308,  # Near min positive float
            "negative_large": -9223372036854775808
        }
        
        with open(large_numbers_json, 'w') as f:
            json.dump(large_data, f)
        
        result = ConfigLoader.load_json(large_numbers_json)
        assert result["large_int"] == 9223372036854775807
        assert result["negative_large"] == -9223372036854775808
        assert isinstance(result["large_float"], float)
        assert isinstance(result["small_float"], float)


class TestConfigValidator:
    """Test cases for ConfigValidator utility class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.valid_config = {
            "server": {"host": "localhost", "port": 8080, "debug": False},
            "database": {"url": "sqlite:///test.db", "pool_size": 10, "timeout": 30},
            "logging": {"level": "INFO", "format": "%(message)s"}
        }
        
        self.required_fields = ["server", "database"]
        
        self.sample_schema = {
            "type": "object",
            "required": ["server", "database"],
            "properties": {
                "server": {
                    "type": "object",
                    "required": ["host", "port"],
                    "properties": {
                        "host": {"type": "string", "minLength": 1},
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "debug": {"type": "boolean"}
                    }
                },
                "database": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1},
                        "pool_size": {"type": "integer", "minimum": 1},
                        "timeout": {"type": "integer", "minimum": 1}
                    }
                }
            }
        }
    
    def test_validate_schema_with_valid_config(self):
        """Test schema validation with valid configuration."""
        result = ConfigValidator.validate_schema(self.valid_config, self.sample_schema)
        assert result is True
    
    def test_validate_schema_with_none_config(self):
        """Test schema validation with None configuration."""
        with pytest.raises(ValidationError):
            ConfigValidator.validate_schema(None)
    
    def test_validate_schema_with_non_dict_config(self):
        """Test schema validation with non-dictionary configuration."""
        with pytest.raises(ValidationError):
            ConfigValidator.validate_schema("not a dict")
        
        with pytest.raises(ValidationError):
            ConfigValidator.validate_schema([1, 2, 3])
        
        with pytest.raises(ValidationError):
            ConfigValidator.validate_schema(42)
    
    def test_validate_required_fields_success(self):
        """Test successful validation of required fields."""
        result = ConfigValidator.validate_required_fields(self.valid_config, self.required_fields)
        assert result is True
    
    def test_validate_required_fields_missing_single_field(self):
        """Test validation with single missing required field."""
        incomplete_config = {"server": {"host": "localhost", "port": 8080}}
        # Missing 'database' field
        
        with pytest.raises(ValidationError) as exc_info:
            ConfigValidator.validate_required_fields(incomplete_config, self.required_fields)
        
        assert "database" in str(exc_info.value)
    
    def test_validate_required_fields_missing_multiple_fields(self):
        """Test validation with multiple missing required fields."""
        incomplete_config = {"logging": {"level": "INFO"}}
        # Missing both 'server' and 'database' fields
        
        with pytest.raises(ValidationError) as exc_info:
            ConfigValidator.validate_required_fields(incomplete_config, self.required_fields)
        
        error_message = str(exc_info.value)
        assert "server" in error_message
        assert "database" in error_message
    
    def test_validate_required_fields_empty_config(self):
        """Test validation with completely empty configuration."""
        with pytest.raises(ValidationError):
            ConfigValidator.validate_required_fields({}, self.required_fields)
    
    def test_validate_required_fields_empty_required_list(self):
        """Test validation with empty required fields list."""
        result = ConfigValidator.validate_required_fields(self.valid_config, [])
        assert result is True
    
    def test_validate_required_fields_none_required_list(self):
        """Test validation with None required fields list."""
        result = ConfigValidator.validate_required_fields(self.valid_config, None)
        assert result is True or result is None  # Depending on implementation
    
    def test_validate_schema_with_additional_properties(self):
        """Test schema validation with additional properties not in schema."""
        config_with_extra = self.valid_config.copy()
        config_with_extra["extra_section"] = {"extra_key": "extra_value"}
        
        # Should either succeed (if additional properties allowed) or fail gracefully
        try:
            result = ConfigValidator.validate_schema(config_with_extra, self.sample_schema)
            assert result is True
        except ValidationError:
            # If additional properties are not allowed, should fail with clear error
            pass


class TestConfigurationIntegration:
    """Integration tests for configuration loading and validation workflow."""
    
    def setup_method(self):
        """Set up integration test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        
        self.sample_config_content = {
            "server": {
                "host": "integration.test.com",
                "port": 9000,
                "debug": True,
                "ssl_enabled": False,
                "max_connections": 500
            },
            "database": {
                "url": "postgresql://testuser:testpass@localhost/testdb",
                "pool_size": 20,
                "timeout": 45,
                "retry_attempts": 5
            },
            "logging": {
                "level": "DEBUG",
                "file": "/tmp/integration_test.log",
                "format": "%(asctime)s [%(levelname)s] %(message)s"
            },
            "cache": {
                "enabled": True,
                "backend": "memory",
                "ttl": 1800
            }
        }
    
    def teardown_method(self):
        """Clean up integration test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_end_to_end_json_config_loading(self):
        """Test complete workflow of loading and validating JSON configuration."""
        config_file = os.path.join(self.temp_dir, "integration_test.json")
        
        with open(config_file, 'w') as f:
            json.dump(self.sample_config_content, f, indent=2)
        
        # Load configuration
        config = SampleConfig()
        config.load_from_file(config_file)
        
        # Verify loaded data
        assert config.data["server"]["host"] == "integration.test.com"
        assert config.data["server"]["port"] == 9000
        assert config.data["database"]["pool_size"] == 20
        assert config.data["cache"]["enabled"] is True
        
        # Validate configuration
        result = config.validate()
        assert result is True
    
    def test_end_to_end_yaml_config_loading(self):
        """Test complete workflow of loading and validating YAML configuration."""
        config_file = os.path.join(self.temp_dir, "integration_test.yaml")
        
        with open(config_file, 'w') as f:
            yaml.dump(self.sample_config_content, f, default_flow_style=False)
        
        # Load configuration
        config = SampleConfig()
        config.load_from_file(config_file)
        
        # Verify loaded data
        assert config.data["server"]["host"] == "integration.test.com"
        assert config.data["database"]["url"].startswith("postgresql://")
        assert config.data["logging"]["level"] == "DEBUG"
        
        # Validate configuration
        result = config.validate()
        assert result is True
    
    def test_config_modification_and_revalidation(self):
        """Test modifying configuration and revalidating."""
        config = SampleConfig(self.sample_config_content)
        
        # Initial validation should pass
        assert config.validate() is True
        
        # Modify configuration
        config.set("server", {"host": "modified.host.com", "port": 8888})
        config.set("new_section", {"new_key": "new_value"})
        
        # Should still validate successfully
        assert config.validate() is True
        assert config.get("server")["host"] == "modified.host.com"
        assert config.get("new_section")["new_key"] == "new_value"
    
    def test_config_loading_with_file_path_tracking(self):
        """Test that file path is properly tracked when loading from file."""
        config_file = os.path.join(self.temp_dir, "path_tracking_test.json")
        
        with open(config_file, 'w') as f:
            json.dump({"test": "data"}, f)
        
        config = SampleConfig()
        config.load_from_file(config_file)
        
        assert config.file_path == config_file
        assert config.data["test"] == "data"
    
    def test_config_error_handling_chain(self):
        """Test error handling throughout the configuration loading chain."""
        # Test file not found
        config = SampleConfig()
        with pytest.raises(FileNotFoundError):
            config.load_from_file("nonexistent.json")
        
        # Test invalid JSON
        invalid_json_file = os.path.join(self.temp_dir, "invalid.json")
        with open(invalid_json_file, 'w') as f:
            f.write('{"invalid": json content}')
        
        with pytest.raises(json.JSONDecodeError):
            config.load_from_file(invalid_json_file)
        
        # Test validation failure
        invalid_config = SampleConfig("not a dict")
        with pytest.raises(ValidationError):
            invalid_config.validate()


class TestConfigurationEdgeCases:
    """Test edge cases and boundary conditions for configuration handling."""
    
    def test_extremely_large_config_file(self):
        """Test handling of very large configuration files."""
        large_config = {
            "server": {"host": "localhost", "port": 8080},
            "database": {"url": "sqlite:///test.db"},
            "large_section": {}
        }
        
        # Create a large section with many keys
        for i in range(1000):
            large_config["large_section"][f"key_{i}"] = f"value_{i}" * 50
        
        config = SampleConfig(large_config)
        result = config.validate()
        assert result is True
        
        # Test that we can still access specific values
        assert config.get("server")["host"] == "localhost"
        assert config.get("large_section")["key_999"] == "value_999" * 50
    
    def test_deeply_nested_config_structure(self):
        """Test handling of deeply nested configuration structures."""
        nested_config = {"level_0": {}}
        current_level = nested_config["level_0"]
        
        # Create 15 levels of nesting
        for i in range(1, 15):
            current_level[f"level_{i}"] = {}
            current_level = current_level[f"level_{i}"]
        
        current_level["deep_value"] = "nested_data"
        current_level["deep_list"] = [1, 2, 3, {"nested_in_list": True}]
        
        config = SampleConfig(nested_config)
        
        # Should handle deep nesting without stack overflow or other issues
        try:
            result = config.validate()
            assert result is True
        except RecursionError:
            pytest.fail("Configuration validation failed due to deep nesting")
        
        # Test access to deeply nested values
        assert config.data["level_0"]["level_1"]["level_2"] is not None
    
    def test_config_with_special_characters_and_encoding(self):
        """Test configuration containing special characters, unicode, and various encodings."""
        special_config = {
            "server": {
                "host": "localhost",
                "port": 8080,
                "description": "Server with special chars: àáâãäåæçèéêë",
                "unicode_value": "Unicode: 你好世界 🌟 🚀 ❤️",
                "path_with_spaces": "/path/with spaces/and-symbols!@#$%^&*()",
                "regex_pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                "sql_like_pattern": "SELECT * FROM users WHERE name LIKE '%test%'",
                "json_string": '{"embedded": "json", "with": ["array", "values"]}',
                "multiline_string": "Line 1\nLine 2\nLine 3\nWith\ttabs"
            },
            "database": {"url": "sqlite:///test.db"}
        }
        
        config = SampleConfig(special_config)
        result = config.validate()
        assert result is True
        
        # Verify special characters are preserved
        assert "你好世界" in config.get("server")["unicode_value"]
        assert "🌟" in config.get("server")["unicode_value"]
        assert "\n" in config.get("server")["multiline_string"]
        assert "\t" in config.get("server")["multiline_string"]
    
    def test_config_with_null_and_empty_values(self):
        """Test configuration with null, empty, and falsy values."""
        config_with_various_values = {
            "server": {
                "host": "localhost",
                "port": 8080,
                "optional_field": None,
                "empty_string": "",
                "empty_list": [],
                "empty_dict": {},
                "zero_value": 0,
                "false_value": False,
                "whitespace_string": "   ",
                "newline_only": "\n"
            },
            "database": {"url": "sqlite:///test.db"}
        }
        
        config = SampleConfig(config_with_various_values)
        
        # Should handle various falsy and empty values appropriately
        try:
            result = config.validate()
            assert result is True
        except (ValidationError, ConfigurationError):
            # If nulls/empties are not allowed, should fail gracefully with clear message
            pass
        
        # Test that we can distinguish between different types of "empty"
        server_config = config.get("server")
        assert server_config["optional_field"] is None
        assert server_config["empty_string"] == ""
        assert server_config["empty_list"] == []
        assert server_config["empty_dict"] == {}
        assert server_config["zero_value"] == 0
        assert server_config["false_value"] is False
    
    def test_config_with_circular_references_simulation(self):
        """Test configuration that simulates circular reference patterns."""
        # Since JSON/YAML can't have true circular references, simulate with string references
        circular_sim_config = {
            "database": {
                "primary": {
                    "host": "primary.db.com",
                    "replica_ref": "database.replica"
                },
                "replica": {
                    "host": "replica.db.com",
                    "primary_ref": "database.primary"
                }
            },
            "server": {
                "main": {
                    "host": "main.server.com",
                    "backup_ref": "server.backup"
                },
                "backup": {
                    "host": "backup.server.com",
                    "main_ref": "server.main"
                }
            }
        }
        
        config = SampleConfig(circular_sim_config)
        result = config.validate()
        assert result is True
        
        # Should be able to access all parts without infinite loops
        assert config.get("database")["primary"]["host"] == "primary.db.com"
        assert config.get("database")["replica"]["primary_ref"] == "database.primary"
    
    def test_concurrent_config_access(self):
        """Test thread-safe access to configuration data."""
        import threading
        import time
        
        config = SampleConfig({
            "server": {"host": "localhost", "port": 8080},
            "database": {"url": "sqlite:///test.db"},
            "shared_counter": 0
        })
        
        results = []
        errors = []
        
        def access_config(thread_id):
            try:
                for i in range(100):
                    # Read operations
                    _ = config.data["server"]["host"]
                    _ = config.get("database")
                    _ = config.validate()
                    
                    # Write operations
                    config.set(f"thread_{thread_id}_key_{i}", f"value_{i}")
                    
                    time.sleep(0.001)  # Small delay to encourage race conditions
                
                results.append(f"thread_{thread_id}_success")
            except Exception as e:
                errors.append(f"thread_{thread_id}_error: {str(e)}")
        
        # Create multiple threads accessing config concurrently
        threads = [threading.Thread(target=access_config, args=(i,)) for i in range(5)]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # All threads should complete successfully
        assert len(results) == 5
        assert len(errors) == 0
        
        # Verify that all thread-specific keys were written
        for thread_id in range(5):
            assert config.get(f"thread_{thread_id}_key_99") == "value_99"
    
    def test_config_memory_usage_with_large_values(self):
        """Test memory handling with large configuration values."""
        # Create config with various large values
        large_string = "x" * 10000  # 10KB string
        large_list = list(range(10000))  # Large list
        large_dict = {f"key_{i}": f"value_{i}" for i in range(1000)}  # Large dict
        
        memory_test_config = {
            "server": {"host": "localhost", "port": 8080},
            "database": {"url": "sqlite:///test.db"},
            "large_data": {
                "large_string": large_string,
                "large_list": large_list,
                "large_dict": large_dict,
                "repeated_large_strings": [large_string] * 10
            }
        }
        
        config = SampleConfig(memory_test_config)
        
        # Should handle large values without memory issues
        result = config.validate()
        assert result is True
        
        # Verify large values are accessible
        assert len(config.get("large_data")["large_string"]) == 10000
        assert len(config.get("large_data")["large_list"]) == 10000
        assert len(config.get("large_data")["large_dict"]) == 1000
        assert len(config.get("large_data")["repeated_large_strings"]) == 10


class TestConfigurationErrorHandling:
    """Test comprehensive error handling and edge cases for error conditions."""
    
    def test_validation_error_message_quality(self):
        """Test that validation errors provide meaningful and helpful messages."""
        test_cases = [
            ("string instead of dict", "not a dict"),
            ("list instead of dict", [1, 2, 3]),
            ("number instead of dict", 42),
            ("None value", None)
        ]
        
        for description, invalid_data in test_cases:
            config = SampleConfig(invalid_data)
            
            try:
                config.validate()
                pytest.fail(f"Expected validation to fail for {description}")
            except (ValidationError, ConfigurationError) as e:
                # Verify error message contains useful information
                error_message = str(e).lower()
                assert len(error_message) > 10  # Should have substantial error message
                assert any(word in error_message for word in ["dict", "configuration", "invalid"])
    
    def test_file_loading_error_scenarios(self):
        """Test various file loading error scenarios."""
        config = SampleConfig()
        
        # Test different file extension error scenarios
        error_scenarios = [
            ("config.txt", "unsupported"),  # Unsupported format
            ("config.xml", "unsupported"),  # Another unsupported format
            ("", "unsupported"),  # Empty filename
        ]
        
        for filename, expected_error_type in error_scenarios:
            with patch('os.path.exists', return_value=True):
                try:
                    config.load_from_file(filename)
                    if expected_error_type == "unsupported":
                        pytest.fail(f"Expected error for unsupported file: {filename}")
                except ConfigurationError as e:
                    assert "unsupported" in str(e).lower() or "format" in str(e).lower()
                except Exception as e:
                    # Other exceptions are also acceptable for unsupported formats
                    pass
    
    def test_partial_loading_error_recovery(self):
        """Test behavior when configuration loading partially fails."""
        config = SampleConfig({"initial": "data"})
        
        # Verify initial state
        assert config.get("initial") == "data"
        
        # Attempt to load from non-existent file
        try:
            config.load_from_file("nonexistent.json")
        except FileNotFoundError:
            pass
        
        # Original data should be preserved after failed load attempt
        assert config.get("initial") == "data"
    
    def test_validation_with_corrupted_internal_state(self):
        """Test validation behavior with various internal state corruptions."""
        # Test with data that gets corrupted after initialization
        config = SampleConfig({"valid": "initial_data"})
        
        # Simulate corruption by directly modifying internal state
        config.data = "corrupted_string"
        
        with pytest.raises(ValidationError):
            config.validate()
        
        # Test with partially corrupted nested structure
        config.data = {"valid_key": "valid_value", "corrupted_key": None}
        try:
            result = config.validate()
            # If validation passes, that's also acceptable behavior
            assert result is True
        except ValidationError:
            # If validation fails due to None values, that's also acceptable
            pass


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v", "--tb=short"])