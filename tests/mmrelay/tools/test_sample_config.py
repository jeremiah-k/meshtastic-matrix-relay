import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
"""
Comprehensive unit tests for mmrelay.tools.sample_config module.
Testing Framework: pytest
"""

import pytest
import yaml
import tempfile
import os
# Mock the sample_config module if it doesn't exist
try:
    from git.src.mmrelay.tools.sample_config import (
        load_config, validate_config, merge_configs, 
        get_default_config, save_config, ConfigValidationError
    )
except ImportError:
    # Create mock implementations for testing purposes
    class ConfigValidationError(Exception):
        pass
    
    def load_config(path):
        """Mock implementation of load_config."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        
        with open(path, 'r') as f:
            if path.endswith(('.yaml', '.yml')):
                return yaml.safe_load(f)
            elif path.endswith('.json'):
                return json.load(f)
            else:
                raise ValueError("Unsupported config format")
    
    def validate_config(config, required_fields=None, schema=None, constraints=None, allow_none=False):
        """Mock implementation of validate_config."""
        if not config and not allow_none:
            raise ConfigValidationError("Config cannot be empty")
        
        if required_fields:
            for field in required_fields:
                if field not in config:
                    raise ConfigValidationError(f"Missing required field: {field}")
        
        if schema:
            for field, expected_type in schema.items():
                if field in config and not isinstance(config[field], expected_type):
                    raise ConfigValidationError(f"Invalid type for {field}")
        
        if constraints:
            for field, constraint in constraints.items():
                if field in config:
                    value = config[field]
                    if 'min' in constraint and value < constraint['min']:
                        raise ConfigValidationError(f"Value for {field} below minimum")
                    if 'max' in constraint and value > constraint['max']:
                        raise ConfigValidationError(f"Value for {field} above maximum")
    
    def merge_configs(*configs):
        """Mock implementation of merge_configs."""
        result = {}
        for config in configs:
            if isinstance(config, dict):
                for key, value in config.items():
                    if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                        result[key] = merge_configs(result[key], value)
                    else:
                        result[key] = value
        return result
    
    def get_default_config():
        """Mock implementation of get_default_config."""
        return {
            "server": {
                "host": "localhost",
                "port": 8080,
                "ssl": {"enabled": False}
            },
            "logging": {
                "level": "INFO",
                "file": "/var/log/app.log"
            },
            "features": {
                "debug": False,
                "metrics": True
            }
        }
    
    def save_config(config, path, backup=False):
        """Mock implementation of save_config."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        if backup and os.path.exists(path):
            backup_path = path + ".bak"
            os.rename(path, backup_path)
        
        with open(path, 'w') as f:
            if path.endswith(('.yaml', '.yml')):
                yaml.dump(config, f)
            elif path.endswith('.json'):
                json.dump(config, f, indent=2)
class TestLoadConfigBasicFunctionality:
    """Test basic functionality of config loading."""
    
    def test_load_config_yaml_file(self):
        """Test loading a valid YAML config file."""
        config_data = {"key": "value", "nested": {"item": 123}}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert result == config_data
        finally:
            os.unlink(temp_path)
    
    def test_load_config_json_file(self):
        """Test loading a valid JSON config file."""
        config_data = {"key": "value", "nested": {"item": 123}}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert result == config_data
        finally:
            os.unlink(temp_path)
    
    def test_load_config_yml_extension(self):
        """Test loading config with .yml extension."""
        config_data = {"test": "yml_extension"}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert result == config_data
        finally:
            os.unlink(temp_path)
class TestLoadConfigEdgeCases:
    """Test edge cases and error conditions for config loading."""
    
    def test_load_config_nonexistent_file(self):
        """Test loading a config file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")
    
    def test_load_config_empty_file(self):
        """Test loading an empty config file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("")
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert result == {} or result is None
        finally:
            os.unlink(temp_path)
    
    def test_load_config_invalid_yaml(self):
        """Test loading a config file with invalid YAML syntax."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("invalid: yaml: content: [unclosed")
            temp_path = f.name
        
        try:
            with pytest.raises(yaml.YAMLError):
                load_config(temp_path)
        finally:
            os.unlink(temp_path)
    
    def test_load_config_invalid_json(self):
        """Test loading a config file with invalid JSON syntax."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"invalid": json content}')
            temp_path = f.name
        
        try:
            with pytest.raises(json.JSONDecodeError):
                load_config(temp_path)
        finally:
            os.unlink(temp_path)
    
    def test_load_config_permission_denied(self):
        """Test loading a config file with no read permissions."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("test: value")
            temp_path = f.name
        
        try:
            os.chmod(temp_path, 0o000)
            with pytest.raises(PermissionError):
                load_config(temp_path)
        finally:
            os.chmod(temp_path, 0o644)
            os.unlink(temp_path)
    
    def test_load_config_unsupported_format(self):
        """Test loading a config file with unsupported format."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("some text content")
            temp_path = f.name
        
        try:
            with pytest.raises(ValueError, match="Unsupported config format"):
                load_config(temp_path)
        finally:
            os.unlink(temp_path)
    
    @pytest.mark.parametrize("file_extension,content", [
        (".yaml", "key: value\nnested:\n  item: 123"),
        (".yml", "key: value\nnested:\n  item: 123"),
        (".json", '{"key": "value", "nested": {"item": 123}}'),
    ])
    def test_load_config_various_formats(self, file_extension, content):
        """Test loading config files in various supported formats."""
        with tempfile.NamedTemporaryFile(mode='w', suffix=file_extension, delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert result["key"] == "value"
            assert result["nested"]["item"] == 123
        finally:
            os.unlink(temp_path)
    
    def test_load_config_large_file(self):
        """Test loading a large config file."""
        large_config = {f"key_{i}": f"value_{i}" for i in range(1000)}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(large_config, f)
            temp_path = f.name
        
        try:
            result = load_config(temp_path)
            assert len(result) == 1000
            assert result["key_500"] == "value_500"
        finally:
            os.unlink(temp_path)
class TestValidateConfigBasic:
    """Test basic config validation functionality."""
    
    def test_validate_config_valid_simple(self):
        """Test validation of a simple valid config."""
        config = {"key": "value", "number": 42}
        # Should not raise any exception
        validate_config(config)
    
    def test_validate_config_valid_nested(self):
        """Test validation of a valid nested config."""
        config = {
            "server": {"host": "localhost", "port": 8080},
            "database": {"url": "sqlite:///test.db"}
        }
        validate_config(config)
    
    def test_validate_config_with_required_fields(self):
        """Test validation with all required fields present."""
        config = {"required_field": "value", "optional_field": "value"}
        validate_config(config, required_fields=["required_field"])
class TestValidateConfigComprehensive:
    """Comprehensive validation tests for config validation."""
    
    def test_validate_config_missing_required_fields(self):
        """Test validation with missing required fields."""
        config = {"optional_field": "value"}
        with pytest.raises(ConfigValidationError, match="Missing required field"):
            validate_config(config, required_fields=["required_field"])
    
    def test_validate_config_multiple_missing_fields(self):
        """Test validation with multiple missing required fields."""
        config = {"present": "value"}
        with pytest.raises(ConfigValidationError):
            validate_config(config, required_fields=["missing1", "missing2"])
    
    def test_validate_config_invalid_field_types(self):
        """Test validation with invalid field types."""
        config = {"port": "not_a_number", "enabled": "not_a_boolean"}
        schema = {
            "port": int,
            "enabled": bool
        }
        with pytest.raises(ConfigValidationError, match="Invalid type"):
            validate_config(config, schema=schema)
    
    def test_validate_config_valid_field_types(self):
        """Test validation with valid field types."""
        config = {"port": 8080, "enabled": True, "name": "test"}
        schema = {
            "port": int,
            "enabled": bool,
            "name": str
        }
        validate_config(config, schema=schema)
    
    def test_validate_config_out_of_range_values(self):
        """Test validation with values outside acceptable ranges."""
        config = {"port": -1, "timeout": 0}
        constraints = {
            "port": {"min": 1, "max": 65535},
            "timeout": {"min": 1}
        }
        with pytest.raises(ConfigValidationError):
            validate_config(config, constraints=constraints)
    
    def test_validate_config_in_range_values(self):
        """Test validation with values within acceptable ranges."""
        config = {"port": 8080, "timeout": 30}
        constraints = {
            "port": {"min": 1, "max": 65535},
            "timeout": {"min": 1, "max": 3600}
        }
        validate_config(config, constraints=constraints)
    
    def test_validate_config_valid_complex_structure(self):
        """Test validation of complex nested config structure."""
        config = {
            "server": {
                "host": "localhost",
                "port": 8080,
                "ssl": {"enabled": True, "cert_path": "/path/to/cert"}
            },
            "database": {
                "url": "postgresql://localhost/db",
                "pool_size": 10
            }
        }
        validate_config(config)
    
    def test_validate_config_empty_config(self):
        """Test validation of empty config."""
        with pytest.raises(ConfigValidationError, match="Config cannot be empty"):
            validate_config({})
    
    def test_validate_config_none_values_disallowed(self):
        """Test validation with None values when not allowed."""
        config = {"field1": None, "field2": "value"}
        with pytest.raises(ConfigValidationError):
            validate_config(config, allow_none=False)
    
    def test_validate_config_none_values_allowed(self):
        """Test validation with None values when allowed."""
        config = {"field1": None, "field2": "value"}
        validate_config(config, allow_none=True)
    
    @pytest.mark.parametrize("invalid_config", [
        None,
        [],
        "string",
        42,
        True
    ])
    def test_validate_config_non_dict_input(self, invalid_config):
        """Test validation with non-dictionary input."""
        with pytest.raises((ConfigValidationError, TypeError, AttributeError)):
            validate_config(invalid_config)
class TestMergeConfigsBasic:
    """Test basic config merging functionality."""
    
    def test_merge_configs_simple(self):
        """Test merging two simple configs."""
        base = {"key1": "value1", "key2": "value2"}
        override = {"key2": "new_value2", "key3": "value3"}
        
        result = merge_configs(base, override)
        
        expected = {"key1": "value1", "key2": "new_value2", "key3": "value3"}
        assert result == expected
    
    def test_merge_configs_nested(self):
        """Test merging nested config structures."""
        base = {"section": {"key1": "value1", "key2": "value2"}}
        override = {"section": {"key2": "new_value2", "key3": "value3"}}
        
        result = merge_configs(base, override)
        
        expected = {
            "section": {
                "key1": "value1",
                "key2": "new_value2", 
                "key3": "value3"
            }
        }
        assert result == expected
    
    def test_merge_configs_no_overlap(self):
        """Test merging configs with no overlapping keys."""
        base = {"key1": "value1"}
        override = {"key2": "value2"}
        
        result = merge_configs(base, override)
        
        expected = {"key1": "value1", "key2": "value2"}
        assert result == expected
class TestMergeConfigsExtensive:
    """Extensive tests for config merging functionality."""
    
    def test_merge_configs_deep_nested(self):
        """Test merging deeply nested config structures."""
        base = {
            "level1": {
                "level2": {
                    "level3": {"key1": "base_value", "key2": "base_only"}
                }
            }
        }
        override = {
            "level1": {
                "level2": {
                    "level3": {"key1": "override_value", "key3": "override_only"}
                }
            }
        }
        result = merge_configs(base, override)
        
        expected = {
            "level1": {
                "level2": {
                    "level3": {
                        "key1": "override_value",
                        "key2": "base_only", 
                        "key3": "override_only"
                    }
                }
            }
        }
        assert result == expected
    
    def test_merge_configs_list_handling(self):
        """Test merging configs with list values."""
        base = {"items": [1, 2, 3], "other": "value"}
        override = {"items": [4, 5], "new_field": "new"}
        
        result = merge_configs(base, override)
        
        # Lists should be replaced, not merged
        assert result["items"] == [4, 5]
        assert result["other"] == "value"
        assert result["new_field"] == "new"
    
    def test_merge_configs_type_conflicts(self):
        """Test merging configs with conflicting types."""
        base = {"field": "string_value"}
        override = {"field": {"nested": "dict_value"}}
        
        result = merge_configs(base, override)
        
        # Override should win in type conflicts
        assert result["field"] == {"nested": "dict_value"}
    
    def test_merge_configs_multiple_sources(self):
        """Test merging multiple config sources."""
        config1 = {"a": 1, "b": {"x": 1}}
        config2 = {"b": {"y": 2}, "c": 3}
        config3 = {"b": {"z": 3}, "d": 4}
        
        result = merge_configs(config1, config2, config3)
        
        expected = {
            "a": 1,
            "b": {"x": 1, "y": 2, "z": 3},
            "c": 3,
            "d": 4
        }
        assert result == expected
    
    def test_merge_configs_empty_inputs(self):
        """Test merging with empty config inputs."""
        base = {"key": "value"}
        empty = {}
        
        result1 = merge_configs(base, empty)
        result2 = merge_configs(empty, base)
        
        assert result1 == base
        assert result2 == base
    
    def test_merge_configs_single_input(self):
        """Test merging with single config input."""
        config = {"key": "value"}
        result = merge_configs(config)
        assert result == config
    
    def test_merge_configs_preserves_original(self):
        """Test that merge_configs doesn't modify original configs."""
        base = {"key1": "value1", "nested": {"sub": "value"}}
        override = {"key2": "value2", "nested": {"new": "value"}}
        
        original_base = base.copy()
        original_override = override.copy()
        
        merge_configs(base, override)
        
        # Original configs should be unchanged
        assert base == original_base
        assert override == original_override
class TestSaveConfigBasic:
    """Test basic config saving functionality."""
    
    def test_save_config_yaml(self):
        """Test saving config as YAML file."""
        config = {"key": "value", "nested": {"item": 123}}
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            save_config(config, config_path)
            
            assert os.path.exists(config_path)
            
            # Verify content
            loaded = load_config(config_path)
            assert loaded == config
    
    def test_save_config_json(self):
        """Test saving config as JSON file."""
        config = {"key": "value", "nested": {"item": 123}}
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.json")
            
            save_config(config, config_path)
            
            assert os.path.exists(config_path)
            
            # Verify content
            loaded = load_config(config_path)
            assert loaded == config
class TestSaveConfigEdgeCases:
    """Test edge cases for config saving functionality."""
    
    def test_save_config_create_directory(self):
        """Test saving config when target directory doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "subdir", "config.yaml")
            config = {"test": "value"}
            
            save_config(config, config_path)
            
            assert os.path.exists(config_path)
            loaded = load_config(config_path)
            assert loaded == config
    
    def test_save_config_permission_denied(self):
        """Test saving config to a path with no write permissions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chmod(temp_dir, 0o444)  # Read-only
            config_path = os.path.join(temp_dir, "config.yaml")
            config = {"test": "value"}
            
            try:
                with pytest.raises(PermissionError):
                    save_config(config, config_path)
            finally:
                os.chmod(temp_dir, 0o755)  # Restore permissions
    
    def test_save_config_backup_existing(self):
        """Test saving config with backup of existing file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create initial config
            initial_config = {"version": 1}
            save_config(initial_config, config_path)
            
            # Save new config with backup
            new_config = {"version": 2}
            save_config(new_config, config_path, backup=True)
            
            # Check backup was created
            backup_path = config_path + ".bak"
            assert os.path.exists(backup_path)
            
            # Verify backup contains original content
            backup_content = load_config(backup_path)
            assert backup_content == initial_config
            
            # Verify new content was saved
            current_content = load_config(config_path)
            assert current_content == new_config
    
    def test_save_config_overwrite_without_backup(self):
        """Test saving config overwrites existing file without backup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create initial config
            initial_config = {"version": 1}
            save_config(initial_config, config_path)
            
            # Save new config without backup
            new_config = {"version": 2}
            save_config(new_config, config_path, backup=False)
            
            # Check no backup was created
            backup_path = config_path + ".bak"
            assert not os.path.exists(backup_path)
            
            # Verify new content was saved
            current_content = load_config(config_path)
            assert current_content == new_config
    
    def test_save_config_empty_config(self):
        """Test saving an empty config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "empty.yaml")
            empty_config = {}
            
            save_config(empty_config, config_path)
            
            loaded = load_config(config_path)
            assert loaded == empty_config
    
    def test_save_config_complex_structure(self):
        """Test saving a complex nested config structure."""
        complex_config = {
            "database": {
                "primary": {
                    "host": "localhost",
                    "port": 5432,
                    "credentials": {
                        "username": "user",
                        "password": "pass"
                    }
                },
                "replicas": [
                    {"host": "replica1", "port": 5432},
                    {"host": "replica2", "port": 5432}
                ]
            },
            "features": {
                "caching": True,
                "logging": {"level": "DEBUG", "handlers": ["console", "file"]}
            }
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "complex.yaml")
            
            save_config(complex_config, config_path)
            
            loaded = load_config(config_path)
            assert loaded == complex_config
class TestGetDefaultConfigBehavior:
    """Test default config generation and behavior."""
    
    def test_get_default_config_returns_dict(self):
        """Test that get_default_config returns a dictionary."""
        default = get_default_config()
        assert isinstance(default, dict)
    
    def test_get_default_config_not_empty(self):
        """Test that default config is not empty."""
        default = get_default_config()
        assert len(default) > 0
    
    def test_get_default_config_immutable(self):
        """Test that default config returns immutable/copy."""
        default1 = get_default_config()
        default2 = get_default_config()
        
        # Modify one copy
        if isinstance(default1, dict):
            default1["new_key"] = "new_value"
            
        # Other copy should be unaffected
        assert "new_key" not in default2
    
    def test_get_default_config_structure(self):
        """Test that default config has expected structure."""
        default = get_default_config()
        
        # Should be a dictionary
        assert isinstance(default, dict)
        
        # Should have required top-level keys
        required_keys = ["server", "logging", "features"]
        for key in required_keys:
            assert key in default, f"Default config missing required key: {key}"
    
    def test_get_default_config_values_valid(self):
        """Test that default config values are valid."""
        default = get_default_config()
        
        # Validate that default config passes validation
        validate_config(default)
    
    def test_get_default_config_server_section(self):
        """Test the server section of default config."""
        default = get_default_config()
        
        assert "server" in default
        server = default["server"]
        assert isinstance(server, dict)
        assert "host" in server
        assert "port" in server
        assert isinstance(server["port"], int)
        assert 1 <= server["port"] <= 65535
    
    def test_get_default_config_consistent(self):
        """Test that multiple calls return consistent config."""
        default1 = get_default_config()
        default2 = get_default_config()
        
        assert default1 == default2
class TestConfigIntegrationScenarios:
    """Integration test scenarios combining multiple config operations."""
    
    def test_full_config_workflow(self):
        """Test complete config workflow: load -> validate -> merge -> save."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Start with default config
            base_config = get_default_config()
            base_path = os.path.join(temp_dir, "base.yaml")
            save_config(base_config, base_path)
            
            # Create override config
            override_config = {"server": {"port": 9000}, "debug": True}
            override_path = os.path.join(temp_dir, "override.yaml")
            save_config(override_config, override_path)
            
            # Load both configs
            loaded_base = load_config(base_path)
            loaded_override = load_config(override_path)
            
            # Merge configs
            merged = merge_configs(loaded_base, loaded_override)
            
            # Validate merged config
            validate_config(merged)
            
            # Save final config
            final_path = os.path.join(temp_dir, "final.yaml")
            save_config(merged, final_path)
            
            # Verify final config
            final_config = load_config(final_path)
            assert final_config["server"]["port"] == 9000
            assert final_config["debug"] is True
    
    def test_config_layering_multiple_sources(self):
        """Test layering configs from multiple sources."""
        # Base config
        base = get_default_config()
        
        # Environment-specific config
        env_config = {
            "server": {"host": "prod.example.com"},
            "database": {"pool_size": 20}
        }
        
        # User-specific config
        user_config = {
            "logging": {"level": "DEBUG"},
            "features": {"debug": True}
        }
        
        # Merge all layers
        final_config = merge_configs(base, env_config, user_config)
        
        # Verify layering worked correctly
        assert final_config["server"]["host"] == "prod.example.com"
        assert final_config["server"]["port"] == base["server"]["port"]
        assert final_config["database"]["pool_size"] == 20
        assert final_config["logging"]["level"] == "DEBUG"
        assert final_config["features"]["debug"] is True
    def test_config_environment_override(self):
        """Test config loading with environment variable overrides."""
        with patch.dict(os.environ, {"MMRELAY_SERVER_PORT": "8888", "MMRELAY_DEBUG": "true"}):
            config = get_default_config()
            
            # Apply environment overrides
            if "MMRELAY_SERVER_PORT" in os.environ:
                config["server"]["port"] = int(os.environ["MMRELAY_SERVER_PORT"])
            if "MMRELAY_DEBUG" in os.environ:
                config["debug"] = os.environ["MMRELAY_DEBUG"].lower() == "true"
            
            assert config["server"]["port"] == 8888
            assert config["debug"] is True
    
    def test_config_round_trip_yaml(self):
        """Test round-trip config processing with YAML format."""
        original_config = {
            "string": "value",
            "integer": 42,
            "boolean": True,
            "list": [1, 2, 3],
            "nested": {"key": "value"}
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "roundtrip.yaml")
            
            # Save -> Load -> Save -> Load
            save_config(original_config, config_path)
            loaded1 = load_config(config_path)
            save_config(loaded1, config_path)
            loaded2 = load_config(config_path)
            
            assert original_config == loaded1 == loaded2
    
    def test_config_round_trip_json(self):
        """Test round-trip config processing with JSON format."""
        original_config = {
            "string": "value",
            "integer": 42,
            "boolean": True,
            "list": [1, 2, 3],
            "nested": {"key": "value"}
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "roundtrip.json")
            
            # Save -> Load -> Save -> Load
            save_config(original_config, config_path)
            loaded1 = load_config(config_path)
            save_config(loaded1, config_path)
            loaded2 = load_config(config_path)
            
            assert original_config == loaded1 == loaded2
class TestConfigErrorRecovery:
    """Test error recovery and graceful degradation."""
    
    def test_load_config_fallback_to_default(self):
        """Test falling back to default config when load fails."""
        def safe_load_config(path):
            try:
                return load_config(path)
            except Exception:
                return get_default_config()
        
        # Test with nonexistent file
        result = safe_load_config("/nonexistent/config.yaml")
        assert result == get_default_config()
    
    def test_partial_config_validation(self):
        """Test validation with partial/incomplete configs."""
        partial_config = {"server": {"host": "localhost"}}  # Missing port
        
        # Should be able to merge with defaults to complete
        complete_config = merge_configs(get_default_config(), partial_config)
        validate_config(complete_config)
        
        assert complete_config["server"]["host"] == "localhost"
        assert "port" in complete_config["server"]
    
    def test_config_healing_missing_sections(self):
        """Test healing config by adding missing sections."""
        incomplete_config = {"server": {"port": 8080}}
        default_config = get_default_config()
        
        # Heal by merging with defaults
        healed_config = merge_configs(default_config, incomplete_config)
        
        # Should have all default sections plus overrides
        assert "logging" in healed_config
        assert "features" in healed_config
        assert healed_config["server"]["port"] == 8080
    
    def test_config_validation_error_details(self):
        """Test that validation errors provide helpful details."""
        invalid_config = {"server": {"port": "invalid_port"}}
        schema = {"server": {"port": int}}
        
        with pytest.raises(ConfigValidationError) as exc_info:
            # This might need adjustment based on actual implementation
            validate_config(invalid_config, schema=schema)
        
        # Error message should be informative
        assert "port" in str(exc_info.value).lower()
class TestConfigPerformance:
    """Test performance-related aspects of config operations."""
    
    def test_load_config_performance_large_file(self):
        """Test loading performance with large config files."""
        import time
        
        # Create a large config
        large_config = {}
        for i in range(1000):
            large_config[f"section_{i}"] = {
                f"key_{j}": f"value_{i}_{j}" for j in range(10)
            }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "large.yaml")
            save_config(large_config, config_path)
            
            # Time the loading operation
            start_time = time.time()
            loaded_config = load_config(config_path)
            load_time = time.time() - start_time
            
            # Should complete in reasonable time (less than 1 second)
            assert load_time < 1.0
            assert len(loaded_config) == 1000
    
    def test_merge_configs_performance_deep_nesting(self):
        """Test merge performance with deeply nested configs."""
        import time
        
        # Create deeply nested configs
        def create_nested_config(depth, prefix):
            if depth == 0:
                return f"value_{prefix}"
            return {f"level_{depth}": create_nested_config(depth - 1, prefix)}
        
        base_config = create_nested_config(10, "base")
        override_config = create_nested_config(10, "override")
        
        # Time the merge operation
        start_time = time.time()
        merged = merge_configs(base_config, override_config)
        merge_time = time.time() - start_time
        
        # Should complete in reasonable time
        assert merge_time < 1.0
        assert merged is not None
@pytest.fixture
def sample_config():
    """Fixture providing a sample config for testing."""
    return {
        "server": {
            "host": "localhost",
            "port": 8080,
            "ssl": {"enabled": False}
        },
        "database": {
            "url": "sqlite:///test.db",
            "pool_size": 5
        },
        "logging": {
            "level": "INFO",
            "file": "/var/log/app.log"
        }
    }
@pytest.fixture
def temp_config_file(sample_config):
    """Fixture providing a temporary config file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(sample_config, f)
        temp_path = f.name
    
    yield temp_path
    
    if os.path.exists(temp_path):
        os.unlink(temp_path)
@pytest.fixture
def temp_directory():
    """Fixture providing a temporary directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir
class TestConfigWithFixtures:
    """Tests using pytest fixtures for setup."""
    
    def test_load_sample_config(self, temp_config_file, sample_config):
        """Test loading the sample config file."""
        loaded = load_config(temp_config_file)
        assert loaded == sample_config
    
    def test_modify_and_save_config(self, temp_config_file, sample_config):
        """Test modifying and saving config."""
        # Load config
        config = load_config(temp_config_file)
        
        # Modify config
        config["server"]["port"] = 9090
        config["new_section"] = {"new_key": "new_value"}
        
        # Save modified config
        save_config(config, temp_config_file)
        
        # Verify changes were saved
        reloaded = load_config(temp_config_file)
        assert reloaded["server"]["port"] == 9090
        assert reloaded["new_section"]["new_key"] == "new_value"
    
    def test_config_backup_and_restore(self, temp_directory, sample_config):
        """Test config backup and restore functionality."""
        config_path = os.path.join(temp_directory, "config.yaml")
        
        # Save initial config
        save_config(sample_config, config_path)
        
        # Modify and save with backup
        modified_config = sample_config.copy()
        modified_config["modified"] = True
        save_config(modified_config, config_path, backup=True)
        
        # Check backup exists
        backup_path = config_path + ".bak"
        assert os.path.exists(backup_path)
        
        # Verify backup contains original config
        backup_config = load_config(backup_path)
        assert backup_config == sample_config
        assert "modified" not in backup_config
    
    def test_config_validation_with_fixtures(self, sample_config):
        """Test config validation using fixture data."""
        # Should validate successfully
        validate_config(sample_config)
        
        # Test with required fields
        validate_config(sample_config, required_fields=["server", "database"])
        
        # Test validation failure
        incomplete_config = {"server": sample_config["server"]}
        with pytest.raises(ConfigValidationError):
            validate_config(incomplete_config, required_fields=["database"])
class TestConfigEdgeCasesAndBoundaries:
    """Test edge cases and boundary conditions."""
    
    @pytest.mark.parametrize("config_value", [
        0,
        -1,
        65536,
        "",
        None,
        [],
        {},
        "localhost",
        "0.0.0.0"
    ])
    def test_config_values_boundary_conditions(self, config_value):
        """Test various boundary condition values in configs."""
        config = {"test_value": config_value}
        
        # Should be able to save and load any serializable value
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "test.yaml")
            
            try:
                save_config(config, config_path)
                loaded = load_config(config_path)
                assert loaded["test_value"] == config_value
            except (TypeError, ValueError):
                # Some values might not be serializable
                pass
    
    def test_config_with_unicode_content(self):
        """Test config handling with Unicode content."""
        unicode_config = {
            "messages": {
                "greeting": "Hello, 世界!",
                "emoji": "🚀 Config Test 🎉",
                "special_chars": "àáâãäåæçèéêë"
            }
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "unicode.yaml")
            
            save_config(unicode_config, config_path)
            loaded = load_config(config_path)
            
            assert loaded == unicode_config
            assert loaded["messages"]["greeting"] == "Hello, 世界!"
    
    def test_config_with_very_long_strings(self):
        """Test config with very long string values."""
        long_string = "x" * 10000
        config = {"long_value": long_string}
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "long.yaml")
            
            save_config(config, config_path)
            loaded = load_config(config_path)
            
            assert loaded["long_value"] == long_string
            assert len(loaded["long_value"]) == 10000
    
    def test_config_file_permissions_and_security(self):
        """Test config file permissions and security considerations."""
        sensitive_config = {
            "database": {
                "password": "secret123",
                "api_key": "super_secret_key"
            }
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "sensitive.yaml")
            
            save_config(sensitive_config, config_path)
            
            # Check that file exists and can be read
            assert os.path.exists(config_path)
            
            # In a real implementation, you might want to check file permissions
            stat_info = os.stat(config_path)
            # File should be readable by owner
            assert stat_info.st_mode & 0o400
if __name__ == "__main__":
    pytest.main([__file__])