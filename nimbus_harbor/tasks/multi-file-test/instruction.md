# Task: Create a Python Package

Create a Python package called `mathutils` in the current working directory with the following structure:

```
mathutils/
    __init__.py
    stats.py
    geometry.py
```

## Requirements

### `stats.py`
- `mean(numbers: list) -> float`: Calculate the arithmetic mean
- `median(numbers: list) -> float`: Calculate the median
- `mode(numbers: list) -> int|float`: Return the most common value (if tie, return smallest)

### `geometry.py`
- `circle_area(radius: float) -> float`: Area of a circle (use math.pi)
- `rectangle_area(width: float, height: float) -> float`: Area of a rectangle
- `triangle_area(base: float, height: float) -> float`: Area of a triangle

### `__init__.py`
- Import and expose all functions from both modules

## Notes
- Do NOT use any external packages (only stdlib)
- All functions should handle edge cases gracefully
