import pytest

from app.deployment import DeploymentConfigurationError, DeploymentSettings


def test_local_mode_is_default():
    settings = DeploymentSettings.from_env({})
    assert settings.data_mode == "local"
    assert settings.process_role == "all"
    settings.validate()


@pytest.mark.parametrize("value", ["", "cluster", "LOCALish"])
def test_unknown_mode_is_rejected(value):
    with pytest.raises(DeploymentConfigurationError, match="APP_DATA_MODE"):
        DeploymentSettings.from_env({"APP_DATA_MODE": value})


def test_distributed_mode_requires_shared_dependencies_and_role():
    settings = DeploymentSettings.from_env({"APP_DATA_MODE": "distributed"})
    with pytest.raises(DeploymentConfigurationError, match="APP_PROCESS_ROLE"):
        settings.validate()

    settings = DeploymentSettings.from_env(
        {"APP_DATA_MODE": "distributed", "APP_PROCESS_ROLE": "api"}
    )
    with pytest.raises(
        DeploymentConfigurationError,
        match="DATABASE_URL, S3_ENDPOINT_URL, S3_BUCKET",
    ):
        settings.validate()


def test_distributed_mode_accepts_complete_api_and_worker_settings():
    values = {
        "APP_DATA_MODE": "distributed",
        "DATABASE_URL": "postgresql://app:secret@postgres/app",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "S3_BUCKET": "objects",
    }
    for role in ("api", "worker"):
        settings = DeploymentSettings.from_env({**values, "APP_PROCESS_ROLE": role})
        settings.validate()
        assert settings.process_role == role
        assert "secret" not in repr(settings.safe_summary())
        assert "secret@" not in repr(settings)


def test_partial_object_credentials_are_rejected():
    values = {
        "APP_DATA_MODE": "distributed",
        "APP_PROCESS_ROLE": "api",
        "DATABASE_URL": "postgresql://app:secret@postgres/app",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "S3_BUCKET": "objects",
        "S3_ACCESS_KEY_ID": "app",
    }
    with pytest.raises(DeploymentConfigurationError, match="configured together"):
        DeploymentSettings.from_env(values).validate()


@pytest.mark.parametrize("name,value", [
    ("APP_PROCESS_ROLE", "scheduler"),
    ("POSTGRES_POOL_MIN_SIZE", "0"),
    ("POSTGRES_POOL_MAX_SIZE", "-1"),
    ("POSTGRES_POOL_MAX_SIZE", "not-an-int"),
])
def test_invalid_role_and_pool_sizes_are_rejected(name, value):
    with pytest.raises(DeploymentConfigurationError):
        DeploymentSettings.from_env({name: value})


def test_pool_minimum_cannot_exceed_maximum():
    with pytest.raises(DeploymentConfigurationError, match="cannot exceed"):
        DeploymentSettings.from_env(
            {"POSTGRES_POOL_MIN_SIZE": "3", "POSTGRES_POOL_MAX_SIZE": "2"}
        )


@pytest.mark.parametrize("name,value", [
    ("DATABASE_URL", "sqlite:///app.db"),
    ("DATABASE_URL", "postgresql://localhost"),
    ("S3_ENDPOINT_URL", "file:///local"),
    ("S3_ENDPOINT_URL", "https://key:secret@objects.example"),
])
def test_distributed_dependencies_reject_wrong_protocols(name, value):
    settings = DeploymentSettings.from_env({
        "APP_DATA_MODE": "distributed", "APP_PROCESS_ROLE": "api",
        "DATABASE_URL": "postgresql://localhost/app", "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_BUCKET": "objects", name: value,
    })
    with pytest.raises(DeploymentConfigurationError):
        settings.validate()
