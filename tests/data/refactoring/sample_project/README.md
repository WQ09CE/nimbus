# Sample API Client Library

A minimal API client library for testing cross-file refactoring capabilities.

## Installation

```bash
pip install sample-api-client
```

## Quick Start

```python
from sample_project.core import APIClient

# Create a client
client = APIClient(base_url="https://api.example.com", api_key="your-key")

# Make API calls using old_api()
response = client.old_api("/users", method="GET")
print(response)

# Create a user
user = client.old_api("/users", method="POST", data={
    "username": "john",
    "email": "john@example.com"
})
```

## API Reference

### APIClient

The main class for interacting with the API.

#### Methods

- `old_api(endpoint, method="GET", data=None, headers=None)`: Make an API request
- `connect()`: Test connection to the API server
- `disconnect()`: Close the connection

#### Example Usage

```python
from sample_project.core import APIClient
from sample_project.core.utils import fetch_all_users, create_user

client = APIClient(base_url="https://api.example.com")

# Using old_api directly
response = client.old_api("/health")

# Using utility functions
users = fetch_all_users(client)
new_user = create_user(client, "alice", "alice@example.com")
```

### AuthService

Authentication service for handling login/logout.

```python
from sample_project.services.auth import AuthService

auth = AuthService(client)
auth.login("username", "password")

if auth.is_authenticated:
    print(f"Logged in as: {auth.current_user}")
```

### DataProcessor

Data transformation service.

```python
from sample_project.services.data import DataProcessor, old_api

# Note: old_api() in data.py is a standalone function
# for legacy data format conversion. It is NOT related
# to APIClient.old_api().

processor = DataProcessor(use_legacy=True)
result = processor.process({"key": "value"})
```

## Migration Guide

When migrating from `old_api()` to `new_api()`:

1. Update all calls to `client.old_api()` to use `client.new_api()`
2. Update documentation references
3. Note: The `old_api()` function in `services/data.py` is unrelated and should NOT be changed

## Testing

```bash
pytest tests/
```

## License

MIT
