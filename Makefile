# Makefile for Meshtastic Matrix Relay Docker operations

# Detect docker compose command (prefer newer 'docker compose' over 'docker-compose')
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: help build build-nocache rebuild run stop logs shell clean config edit setup

# Default target
help:
	@echo "Available Docker commands:"
	@echo "  setup   - Copy sample config and open editor (recommended for first time)"
	@echo "  config  - Copy sample config to ~/.mmrelay/config.yaml"
	@echo "  edit    - Edit the config file with your preferred editor"
	@echo "  build   - Build Docker image (uses layer caching for faster builds)"
	@echo "  build-nocache - Build Docker image with --no-cache for fresh builds"
	@echo "  rebuild - Stop, rebuild with --no-cache, and restart container (for updates)"
	@echo "  run     - Start the container"
	@echo "  stop    - Stop the container (keeps container for restart)"
	@echo "  logs    - Show container logs"
	@echo "  shell   - Access container shell"
	@echo "  clean   - Remove containers and networks"

# Copy sample config to ~/.mmrelay/config.yaml and create Docker files
config:
	@mkdir -p ~/.mmrelay ~/.mmrelay/data ~/.mmrelay/logs
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
	@if [ ! -f docker-compose.yaml ]; then \
		cp src/mmrelay/tools/sample-docker-compose.yaml docker-compose.yaml; \
		echo "docker-compose.yaml created from sample - edit if needed"; \
	else \
		echo "docker-compose.yaml already exists"; \
	fi
	@echo "Created directories: ~/.mmrelay/data and ~/.mmrelay/logs with proper ownership"

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

# Setup: copy config and open editor (recommended for first time)
setup:
	@$(MAKE) config
	@$(MAKE) edit

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
