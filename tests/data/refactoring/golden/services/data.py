"""Data processing service.

This module provides data processing functionality. Note that it has its own
old_api() function which is COMPLETELY UNRELATED to APIClient.old_api().

IMPORTANT: When refactoring APIClient.old_api() to new_api(), the old_api()
function in this module should NOT be modified!
"""

from typing import Any, Dict, List


def old_api(data: Dict[str, Any]) -> Dict[str, Any]:
    """Process data using the old API format.

    This function is for backward compatibility with legacy systems.
    It converts modern data format to the old API format.

    IMPORTANT: This function is NOT related to APIClient.old_api()!
    It should NOT be renamed during the APIClient refactoring.

    Args:
        data: Data in modern format.

    Returns:
        Data converted to old API format.
    """
    # Convert to old format for legacy compatibility
    return {
        "legacy_version": "1.0",
        "payload": data,
        "timestamp": None,  # Old API doesn't support timestamps
    }


def new_api_format(data: Dict[str, Any]) -> Dict[str, Any]:
    """Process data using the new API format.

    Args:
        data: Data in any format.

    Returns:
        Data in new API format.
    """
    return {
        "version": "2.0",
        "data": data,
        "metadata": {},
    }


class DataProcessor:
    """Processor for handling data transformations.

    This class provides methods for transforming data between formats.
    """

    def __init__(self, use_legacy: bool = False):
        """Initialize the data processor.

        Args:
            use_legacy: If True, use old_api format for output.
        """
        self.use_legacy = use_legacy

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data according to configuration.

        Args:
            data: Input data to process.

        Returns:
            Processed data in configured format.
        """
        if self.use_legacy:
            # Use the old_api function for legacy format
            return old_api(data)
        return new_api_format(data)

    def convert_to_legacy(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert data to legacy format.

        Args:
            data: Modern format data.

        Returns:
            Data in legacy format using old_api.
        """
        return old_api(data)

    def batch_process(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process multiple items.

        Args:
            items: List of data items to process.

        Returns:
            List of processed items.
        """
        return [self.process(item) for item in items]
