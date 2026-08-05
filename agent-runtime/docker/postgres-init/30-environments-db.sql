-- Dedicated database + role for the environments service (the opt-in `demo`
-- compose profile's real-env), kept apart from the runtime's projection
-- tables, mirroring the 20-litellm-db.sql convention. Inert for the default
-- stack and test lanes: without --profile demo the database just sits empty.
-- Runs once at first initdb of a fresh volume.
CREATE ROLE convoy_env LOGIN PASSWORD 'convoy_env';
CREATE DATABASE convoy_environments OWNER convoy_env;
