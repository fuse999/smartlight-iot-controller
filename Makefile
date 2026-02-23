# Detect if we're running inside a container
IN_DOCKER := $(shell test -f /.dockerenv && echo 1)

ifeq ($(IN_DOCKER),1)

# === Commands when you're INSIDE the dev container ===
up:
	cd server && python manage.py runserver 0.0.0.0:8000

migrate:
	cd server && python manage.py migrate

createsuperuser:
	cd server && python manage.py createsuperuser

else

# === Commands when you're on WINDOWS / host machine ===
up:
	docker compose up

down:
	docker compose down

build:
	docker compose build

migrate:
	docker compose run --rm server python manage.py migrate

createsuperuser:
	docker compose run --rm server python manage.py createsuperuser

endif
