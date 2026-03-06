import re

with open("tests/test_mmu_image_optimization.py", "r") as f:
    content = f.read()

# Fix TestDowngradeSeenImages setup
content = re.sub(
    r'(class TestDowngradeSeenImages:.*?def setup_method\(self\):)',
    r'\1\n        from nimbus.core.memory.context_assembler import ContextAssembler\n        self.assembler = ContextAssembler(MMU(config=MMUConfig(max_image_tokens=2000)))',
    content,
    flags=re.DOTALL
)

# Replace all leftover self.mmu._downgrade_seen_images with self.assembler._downgrade_seen_images
content = content.replace('self.mmu._downgrade_seen_images', 'self.assembler._downgrade_seen_images')

with open("tests/test_mmu_image_optimization.py", "w") as f:
    f.write(content)
print("Applied fixes")
