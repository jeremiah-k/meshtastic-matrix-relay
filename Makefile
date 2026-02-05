# Makefile for Meshtastic Matrix Relay Docker operations

# Detect docker compose command (prefer newer 'docker compose' over 'docker-compose')
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: all test help build build-nocache rebuild run stop logs shell clean config edit setup setup-prebuilt update-compose doctor paths

# Alias targets for checkmake compliance
all: help

test:
	@echo "Run tests with: python -m pytest -v --cov --tb=short"

# Default target
help:
	@echo "Available Docker commands:"
	@echo "  setup   - Copy sample config and open editor (builds from source)"
	@echo "  setup-prebuilt - Copy sample config for prebuilt images (faster, recommended)"
	@echo "  config  - Copy sample config to ~/.mmrelay/config.yaml"
	@echo "  edit    - Edit the config file with your preferred editor"
	@echo "  update-compose - Update docker-compose.yaml with latest sample"
	@echo "  build   - Build Docker image from source (uses layer caching)"
	@echo "  build-nocache - Build Docker image from source with --no-cache"
	@echo "  rebuild - Stop, rebuild from source with --no-cache, and restart"
	@echo "  run     - Start the container (prebuilt images or built from source)"
	@echo "  stop    - Stop the container (keeps container for restart)"
	@echo "  logs    - Show container logs"
	@echo "  shell   - Access container shell"
	@echo "  doctor  - Run diagnostics inside the container"
	@echo "  paths   - Show runtime paths inside the container"
	@echo "  clean   - Remove containers and networks"

# Internal target for common setup tasks
_setup_common:
	@mkdir -p ~/.mmrelay
	@if [ ! -f ~/.mmrelay/config.yaml ]; then \
		cp src/mmrelay/tools/sample_config.yaml ~/.mmrelay/config.yaml; \
		echo "Sample config copied to ~/.mmrelay/config.yaml - please edit it before running"; \
	else \
		echo "~/.mmrelay/config.yaml already exists"; \
	fi
	@if [ ! -f .env ]; then \
		cp src/mmrelay/tools/sample.env .env; \
		echo ".env file created from sample - edit if needed"; \
	else \
		echo ".env file already exists"; \
	fi
	@echo "Host directory ~/.mmrelay created - will be mounted to /data in the container"

# Copy sample config to ~/.mmrelay/config.yaml and create Docker files
config: _setup_common
	@if [ ! -f docker-compose.yaml ]; then \
		cp src/mmrelay/tools/sample-docker-compose.yaml docker-compose.yaml; \
		echo "docker-compose.yaml created from sample - edit if needed"; \
	else \
		echo "docker-compose.yaml already exists"; \
	fi

# Edit the config file with preferred editor
edit:
	@if [ ! -f ~/.mmrelay/config.yaml ]; then \
		echo "Config file not found. Run 'make config' first."; \
		exit 1; \
	fi
	@if [ -f .env ]; then \
		. ./.env; \
	fi
	@if [ -n "$$EDITOR" ]; then \
		$$EDITOR ~/.mmrelay/config.yaml; \
	else \
		echo "Select your editor:"; \
		echo "1) nano (beginner-friendly) [default]"; \
		echo "2) vim"; \
		echo "3) emacs"; \
		echo "4) code (VS Code)"; \
		echo "5) gedit"; \
		echo "6) other (specify command)"; \
		read -p "Enter choice (1-6, or press Enter for nano): " choice; \
		case "$$choice" in \
			""|1) \
				echo "EDITOR=nano" >> .env; \
				nano ~/.mmrelay/config.yaml ;; \
			2) \
				echo "EDITOR=vim" >> .env; \
				vim ~/.mmrelay/config.yaml ;; \
			3) \
				echo "EDITOR=emacs" >> .env; \
				emacs ~/.mmrelay/config.yaml ;; \
			4) \
				echo "EDITOR=code" >> .env; \
				code ~/.mmrelay/config.yaml ;; \
			5) \
				echo "EDITOR=gedit" >> .env; \
				gedit ~/.mmrelay/config.yaml ;; \
			6) \
				read -p "Enter editor command: " custom_editor; \
				echo "EDITOR=$$custom_editor" >> .env; \
				$$custom_editor ~/.mmrelay/config.yaml ;; \
			*) \
				echo "Invalid choice. Using nano as default."; \
				echo "EDITOR=nano" >> .env; \
				nano ~/.mmrelay/config.yaml ;; \
		esac \
	fi

# Setup: copy config and open editor (builds from source)
setup:
	@$(MAKE) config
	@$(MAKE) edit

# Setup with prebuilt images: copy config and use prebuilt docker-compose
setup-prebuilt: _setup_common
	@if [ ! -f docker-compose.yaml ]; then \
		cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
		echo "docker-compose.yaml created from prebuilt sample - uses official images"; \
	else \
		echo "docker-compose.yaml already exists"; \
	fi
	@echo "Using prebuilt images - no building required, just run 'make run'"
	@$(MAKE) edit

# Update docker-compose.yaml with latest sample
update-compose:
	@if [ -f docker-compose.yaml ]; then \
		echo "Backing up existing docker-compose.yaml to docker-compose.yaml.bak"; \
		cp docker-compose.yaml docker-compose.yaml.bak; \
	fi
	@cp src/mmrelay/tools/sample-docker-compose.yaml docker-compose.yaml
	@echo "Updated docker-compose.yaml with latest sample"
	@echo "Please review and edit for your specific configuration (BLE, serial, etc.)"

# Build the Docker image (uses layer caching for faster builds)
build:
	$(DOCKER_COMPOSE) --progress=plain build

# Build the Docker image with --no-cache for fresh builds
build-nocache:
	$(DOCKER_COMPOSE) --progress=plain build --no-cache

# Stop, rebuild with --no-cache, and restart container (for updates)
rebuild:
	$(DOCKER_COMPOSE) down
	$(DOCKER_COMPOSE) --progress=plain build --no-cache
	UID=$(shell id -u) GID=$(shell id -g) $(DOCKER_COMPOSE) up -d

# Start the container
run:
	UID=$(shell id -u) GID=$(shell id -g) $(DOCKER_COMPOSE) up -d

# Stop the container
stop:
	$(DOCKER_COMPOSE) stop

# Show logs
logs:
	$(DOCKER_COMPOSE) logs -f

# Access container shell
shell:
	$(DOCKER_COMPOSE) exec mmrelay bash

# Remove containers and networks (data in ~/.mmrelay/ is preserved)
clean:
	$(DOCKER_COMPOSE) down

# Run diagnostics inside the container
doctor:
	@echo "Running diagnostics inside container..."
	$(DOCKER_COMPOSE) exec -T mmrelay mmrelay doctor --config /app/config.yaml

# Show runtime paths inside the container
paths:
	@echo "Showing runtime paths inside container..."
	$(DOCKER_COMPOSE) exec -T mmrelay mmrelay paths --config /app/config.yaml
