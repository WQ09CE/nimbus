"""User management with code duplication."""

class UserManager:
    def __init__(self):
        self.users = {}

    def _validate_user(self, user_id, name, email):
        """Validate user data before adding."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        return True

    def add_admin(self, user_id, name, email):
        """Add an admin user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "admin",
            "created_at": "2024-01-01"
        }
        print(f"Admin {name} added successfully")
        return True

    def add_member(self, user_id, name, email):
        """Add a member user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "member",
            "created_at": "2024-01-01"
        }
        print(f"Member {name} added successfully")
        return True

    def add_guest(self, user_id, name, email):
        """Add a guest user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "guest",
            "created_at": "2024-01-01"
        }
        print(f"Guest {name} added successfully")
        return True
