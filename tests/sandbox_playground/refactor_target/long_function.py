"""Data processor with overly long function."""

def process_data(data, options=None):
    """Process data with various transformations.

    This function does too many things and should be split up.
    """
    options = options or {}
    result = []

    # Step 1: Validate data
    if data is None:
        return {"error": "Data is None"}
    if not isinstance(data, list):
        return {"error": "Data must be a list"}
    if len(data) == 0:
        return {"error": "Data is empty"}

    # Step 2: Filter data
    filtered = []
    min_val = options.get("min_value", 0)
    max_val = options.get("max_value", 100)
    for item in data:
        if isinstance(item, (int, float)):
            if min_val <= item <= max_val:
                filtered.append(item)

    # Step 3: Transform data
    transformed = []
    scale = options.get("scale", 1)
    offset = options.get("offset", 0)
    for item in filtered:
        new_val = item * scale + offset
        transformed.append(new_val)

    # Step 4: Aggregate data
    if not transformed:
        return {"error": "No valid data after filtering"}
    total = sum(transformed)
    count = len(transformed)
    average = total / count
    minimum = min(transformed)
    maximum = max(transformed)

    # Step 5: Format output
    result = {
        "count": count,
        "sum": total,
        "average": average,
        "min": minimum,
        "max": maximum,
        "data": transformed
    }

    return result
