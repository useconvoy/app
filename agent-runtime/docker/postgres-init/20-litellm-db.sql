-- Dedicated database + role for the LiteLLM proxy's virtual-key store, kept
-- apart from the runtime's projection tables. Runs once at first initdb of a
-- fresh volume.
CREATE ROLE litellm LOGIN PASSWORD 'litellm';
CREATE DATABASE litellm OWNER litellm;
