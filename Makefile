# Makefile for Meshtastic Matrix Relay Docker operations

# Detect docker compose command (prefer newer 'docker compose' over 'docker-compose')
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: all test help build build-nocache rebuild run stop logs shell clean config edit setup setup-prebuilt update-compose doctor paths use-prebuilt use-source _check_legacy_env _check_legacy_compose _migrate_prompt

# Alias targets for checkmake compliance
all: help

test:
	@echo "Run tests with: python -m pytest -v --cov --tb=short"

# Default target
help:
	@echo "Available Docker commands:"
	@echo "  setup          - Interactive setup (prompts for prebuilt vs source)"
	@echo "  setup-prebuilt - Setup with prebuilt image (recommended)"
	@echo "  use-prebuilt   - Switch to prebuilt image (remove override file)"
	@echo "  use-source     - Switch to build from source (create override file)"
	@echo "  config         - Copy sample config to ~/.mmrelay/config.yaml"
	@echo "  edit           - Edit the config file with your preferred editor"
	@echo "  update-compose - Update docker-compose.yaml with latest sample"
	@echo "  build          - Build Docker image from source (uses layer caching)"
	@echo "  build-nocache  - Build Docker image from source with --no-cache"
	@echo "  rebuild        - Stop, rebuild from source with --no-cache, and restart"
	@echo "  run            - Start the container (prebuilt or source based on override file)"
	@echo "  stop           - Stop the container (keeps container for restart)"
	@echo "  logs           - Show container logs"
	@echo "  shell          - Access container shell"
	@echo "  doctor         - Run diagnostics inside the container"
	@echo "  paths          - Show runtime paths inside the container"
	@echo "  clean          - Remove containers and networks"

# =============================================================================
# Legacy Detection and Migration (v1.2.x → v1.3)
# =============================================================================

# Check for legacy .env file (v1.2.x used MMRELAY_HOME, v1.3 uses MMRELAY_HOST_HOME)
_check_legacy_env:
	@if [ -f .env ]; then \
		if grep -q '^MMRELAY_HOME=' .env 2>/dev/null && ! grep -q '^MMRELAY_HOST_HOME=' .env 2>/dev/null; then \
			echo "LEGACY_ENV=1"; \
		fi; \
	fi

# Check for legacy docker-compose.yaml (v1.2.x used two volume mounts, v1.3 uses single mount)
# Detection: v1.3 has MMRELAY_HOME=/data in environment AND single /data mount
# Legacy lacks MMRELAY_HOME=/data OR has /app/ mounts
_check_legacy_compose:
	@if [ -f docker-compose.yaml ]; then \
		if grep -q 'MMRELAY_HOME=/data' docker-compose.yaml 2>/dev/null; then \
			: v1.3 format detected, not legacy; \
		elif grep -q ':/app/' docker-compose.yaml 2>/dev/null; then \
			echo "LEGACY_COMPOSE=1"; \
		fi; \
	fi

# Prompt user for migration when legacy detected
_migrate_prompt:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║           Legacy Setup Detected - Migration Required             ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Your current setup uses the v1.2.x directory layout which is deprecated."
	@echo ""
	@echo "Changes needed:"
	@if [ -f .env ] && grep -q '^MMRELAY_HOME=' .env 2>/dev/null && ! grep -q '^MMRELAY_HOST_HOME=' .env 2>/dev/null; then \
		echo "  • .env: Replace MMRELAY_HOME with MMRELAY_HOST_HOME"; \
	fi
	@if [ -f docker-compose.yaml ] && grep -q ':/app/' docker-compose.yaml 2>/dev/null; then \
		echo "  • docker-compose.yaml: Update to v1.3 format (single volume mount)"; \
	fi
	@echo ""
	@echo "After updating and starting the container, run:"
	@echo "  docker compose exec mmrelay mmrelay migrate --dry-run"
	@echo "  docker compose exec mmrelay mmrelay migrate"
	@echo "  docker compose exec mmrelay mmrelay verify-migration"
	@echo ""
	@echo "[1] Update files automatically (recommended)"
	@echo "[2] Skip - I'll handle it manually"
	@echo ""
	@read -p "Choose [1-2]: " choice; \
	case "$$choice" in \
		1) \
			echo "MIGRATE=yes"; \
		;; \
		2) \
			echo "MIGRATE=no"; \
		;; \
		*) \
			echo "MIGRATE=yes"; \
		;; \
	esac

# Internal: Handle legacy detection and prompt for migration
_setup_with_migration_check:
	@legacy_env=$$($(MAKE) -s _check_legacy_env); \
	legacy_compose=$$($(MAKE) -s _check_legacy_compose); \
	if [ -n "$$legacy_env" ] || [ -n "$$legacy_compose" ]; then \
		migrate=$$($(MAKE) -s _migrate_prompt); \
		if echo "$$migrate" | grep -q 'MIGRATE=yes'; then \
			echo ""; \
			echo "Updating configuration files..."; \
			if [ -f .env ] && grep -q '^MMRELAY_HOME=' .env 2>/dev/null && ! grep -q '^MMRELAY_HOST_HOME=' .env 2>/dev/null; then \
				echo "Updating .env file..."; \
				sed -i.bak 's/^MMRELAY_HOME=$$HOME$$/MMRELAY_HOST_HOME=$$HOME/' .env 2>/dev/null || true; \
				sed -i.bak 's/^MMRELAY_HOME=/MMRELAY_HOST_HOME=/' .env 2>/dev/null || true; \
				rm -f .env.bak; \
				echo "  ✓ .env updated (MMRELAY_HOME → MMRELAY_HOST_HOME)"; \
			fi; \
			if [ -f docker-compose.yaml ] && grep -q ':/app/' docker-compose.yaml 2>/dev/null; then \
				echo "Replacing docker-compose.yaml with v1.3 format..."; \
				cp docker-compose.yaml docker-compose.yaml.legacy.bak; \
				echo "  ✓ Backup saved to docker-compose.yaml.legacy.bak"; \
			fi; \
		fi; \
	fi

# =============================================================================
# Setup Targets
# =============================================================================

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
config: _setup_common _setup_with_migration_check
	@if [ -f docker-compose.yaml.legacy.bak ]; then \
		cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
		echo "docker-compose.yaml replaced with v1.3 format (prebuilt base)"; \
	elif [ ! -f docker-compose.yaml ]; then \
		cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
		echo "docker-compose.yaml created (uses prebuilt image)"; \
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

# Setup: interactive - prompt for prebuilt vs source build
setup: _setup_common _setup_with_migration_check
	@if [ -f docker-compose.yaml.legacy.bak ] || [ ! -f docker-compose.yaml ]; then \
		echo ""; \
		echo "Select deployment mode:"; \
		echo "  [1] Prebuilt image (recommended - faster, auto-updates available)"; \
		echo "  [2] Build from source (for developers)"; \
		echo ""; \
		read -p "Choose [1-2, default=1]: " mode; \
		case "$$mode" in \
			2) \
				cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
				cp src/mmrelay/tools/sample-docker-compose-override.yaml docker-compose.override.yaml; \
				echo "docker-compose.yaml created (base - prebuilt image)"; \
				echo "docker-compose.override.yaml created (override - build from source)"; \
			;; \
			*) \
				cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
				echo "docker-compose.yaml created (prebuilt image)"; \
			;; \
		esac; \
	else \
		echo "docker-compose.yaml already exists"; \
		if [ -f docker-compose.override.yaml ]; then \
			echo "  Current mode: build from source (override file present)"; \
			echo "  Use 'make use-prebuilt' to switch to prebuilt image"; \
		else \
			echo "  Current mode: prebuilt image"; \
			echo "  Use 'make use-source' to switch to build from source"; \
		fi; \
	fi
	@$(MAKE) edit

# Setup with prebuilt images: copy config and use prebuilt docker-compose
setup-prebuilt: _setup_common _setup_with_migration_check
	@if [ -f docker-compose.yaml.legacy.bak ] || [ ! -f docker-compose.yaml ]; then \
		cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml; \
		echo "docker-compose.yaml created (prebuilt image)"; \
	else \
		echo "docker-compose.yaml already exists"; \
	fi
	@if [ -f docker-compose.override.yaml ]; then \
		echo "Removing docker-compose.override.yaml to use prebuilt image"; \
		rm -f docker-compose.override.yaml; \
	fi
	@echo "Using prebuilt images - no building required, just run 'make run'"
	@$(MAKE) edit

# Switch to prebuilt image (remove override file)
use-prebuilt:
	@if [ ! -f docker-compose.yaml ]; then \
		echo "No docker-compose.yaml found. Run 'make setup' first."; \
		exit 1; \
	fi
	@if [ -f docker-compose.override.yaml ]; then \
		echo "Removing docker-compose.override.yaml to use prebuilt image..."; \
		rm -f docker-compose.override.yaml; \
		echo "✓ Now using prebuilt image: ghcr.io/jeremiah-k/mmrelay:latest"; \
		echo "  Run 'make run' to start with the prebuilt image"; \
	else \
		echo "Already using prebuilt image (no override file)."; \
	fi

# Switch to build from source (create override file)
use-source:
	@if [ ! -f docker-compose.yaml ]; then \
		echo "No docker-compose.yaml found. Run 'make setup' first."; \
		exit 1; \
	fi
	@if [ ! -f docker-compose.override.yaml ]; then \
		echo "Creating docker-compose.override.yaml to build from source..."; \
		cp src/mmrelay/tools/sample-docker-compose-override.yaml docker-compose.override.yaml; \
		echo "✓ Now building from source (local Dockerfile)"; \
		echo "  Run 'make build' then 'make run' to build and start"; \
	else \
		echo "Already building from source (override file exists)."; \
	fi

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
	$(DOCKER_COMPOSE) exec -T mmrelay mmrelay doctor

# Show runtime paths inside the container
paths:
	@echo "Showing runtime paths inside container..."
	$(DOCKER_COMPOSE) exec -T mmrelay mmrelay paths
