from rest_framework import serializers


def validate_password_length(password: str) -> None:
    if len(password) <= 8:
        raise serializers.ValidationError("Password must be more than 8 characters.")
