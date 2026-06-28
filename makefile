.PHONY: help run-api up-redis down-redis logs-redis up-api down-api logs-api up-celery down-celery logs-celery

PROJECT_NAME ?= prs_effectiveness
ENV_FILE ?= src/.env
TAIL ?= 500
FOLLOW ?= 0

DOCKER_COMPOSE = docker compose --env-file $(ENV_FILE) -p $(PROJECT_NAME)
REDIS_COMPOSE = $(DOCKER_COMPOSE) -f deploy/redis/docker-compose.yml
API_COMPOSE = $(DOCKER_COMPOSE) -f deploy/app/api.docker-compose.yml
CELERY_COMPOSE = $(DOCKER_COMPOSE) -f deploy/app/celery.docker-compose.yml
LOG_FOLLOW = $(if $(filter 1 true yes on,$(FOLLOW)),-f,)
LOG_OPTIONS = --tail $(TAIL) $(LOG_FOLLOW)

help: ## Show available make commands with descriptions.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_-]+:.*## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

run-api: ## Run API server locally from src with PYTHONPATH configured.
	cd src && PYTHONPATH=$$(pwd) python entrypoints/server.py

up-redis: ## Build and start Redis in detached mode.
	$(REDIS_COMPOSE) up -d --build

down-redis: ## Stop and remove Redis containers.
	$(REDIS_COMPOSE) down

logs-redis: ## Show Redis logs. Use TAIL=100 and FOLLOW=1 to control output.
	$(REDIS_COMPOSE) logs $(LOG_OPTIONS)

up-api: ## Build and start the API service in detached mode.
	$(API_COMPOSE) up -d --build

down-api: ## Stop and remove API service containers.
	$(API_COMPOSE) down

logs-api: ## Show API service logs. Use TAIL=100 and FOLLOW=1 to control output.
	$(API_COMPOSE) logs $(LOG_OPTIONS) api

up-celery: ## Build and start the Celery beat service in detached mode.
	$(CELERY_COMPOSE) up -d --build

down-celery: ## Stop and remove Celery beat service containers.
	$(CELERY_COMPOSE) down

logs-celery: ## Show Celery beat logs. Use TAIL=100 and FOLLOW=1 to control output.
	$(CELERY_COMPOSE) logs $(LOG_OPTIONS) celery_beat
