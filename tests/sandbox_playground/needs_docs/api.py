"""API module - needs documentation."""

class APIClient:
    def __init__(self, base_url, api_key=None, timeout=30):
        """Initialize an APIClient instance.

        Args:
            base_url (str): The base URL for the API endpoint. Trailing slashes will be removed.
            api_key (str, optional): The API key for authentication. Defaults to None.
            timeout (int, optional): The request timeout in seconds. Defaults to 30.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = None

    def get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass

    def post(self, endpoint, data=None, json=None):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass

    def delete(self, endpoint):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass


def retry(max_attempts=3, delay=1.0, exceptions=(Exception,)):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        import time
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator
