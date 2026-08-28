from foundation.shared.ocr_provider_config import OCR_PROVIDER_CONFIG_ENV
from foundation.shared.ocr_provider_config import PADDLE_DEFAULT_MODEL_ENV
from foundation.shared.ocr_provider_config import PADDLE_DEFAULT_MODEL_FALLBACK
from foundation.shared.ocr_provider_config import configured_command_providers
from foundation.shared.ocr_provider_config import configured_local_command_providers
from foundation.shared.ocr_provider_config import configured_remote_command_providers
from foundation.shared.ocr_provider_config import normalize_paddle_model_name
from foundation.shared.ocr_provider_config import ocr_provider_config
from foundation.shared.ocr_provider_config import ocr_provider_credential_spec
from foundation.shared.ocr_provider_config import ocr_provider_definition
from foundation.shared.ocr_provider_config import ocr_provider_definitions
from foundation.shared.ocr_provider_config import ocr_provider_options
from foundation.shared.ocr_provider_config import paddle_default_model

__all__ = [
    "OCR_PROVIDER_CONFIG_ENV",
    "PADDLE_DEFAULT_MODEL_ENV",
    "PADDLE_DEFAULT_MODEL_FALLBACK",
    "configured_command_providers",
    "configured_local_command_providers",
    "configured_remote_command_providers",
    "normalize_paddle_model_name",
    "ocr_provider_config",
    "ocr_provider_credential_spec",
    "ocr_provider_definition",
    "ocr_provider_definitions",
    "ocr_provider_options",
    "paddle_default_model",
]
