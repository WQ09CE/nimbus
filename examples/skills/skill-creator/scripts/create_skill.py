
import argparse
import sys
from pathlib import Path
import yaml

def main():
    parser = argparse.ArgumentParser(description="Create a new Nimbus Skill")
    parser.add_argument("--name", required=True, help="Skill name")
    parser.add_argument("--description", required=True, help="Skill description")
    parser.add_argument("--path", default="skills", help="Root path containing skills")
    
    args = parser.parse_args()
    
    # Resolve path
    root_path = Path(args.path)
    if not root_path.exists():
        try:
            root_path.mkdir(parents=True)
            print(f"[Info] Created root skills directory: {root_path}")
        except OSError as e:
            print(f"[Error] Failed to create root directory: {e}")
            sys.exit(1)
            
    skill_dir = root_path / args.name
    if skill_dir.exists():
        print(f"[Error] Skill directory '{skill_dir}' already exists.")
        sys.exit(1)
        
    try:
        skill_dir.mkdir(parents=True)
        (skill_dir / "scripts").mkdir()
        
        # Create minimal SKILL.md
        manifest = {
            "name": args.name,
            "version": "1.0.0",
            "description": args.description,
            "tools": []
        }
        
        skill_file = skill_dir / "SKILL.md"
        with open(skill_file, "w") as f:
            f.write("---\n")
            yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)
            f.write("---\n\n")
            f.write(f"# {args.name} Guidelines\n\n")
            f.write(f"Describe how to use the {args.name} tools here.\n")
            
        print(f"[Success] Created skill structure at {skill_dir}")
        print(f"Next steps:")
        print(f"1. Create tools using `AddTool`")
        print(f"2. Add implementation scripts to `{skill_dir}/scripts/`")
        print(f"3. Run `ReloadSkills` to make the new skill available")
        # Emit machine-readable hint for the agent
        abs_skill_dir = skill_dir.resolve()
        abs_root = root_path.resolve()
        print(f"[Hint] To load this skill, call: ReloadSkills(path=\"{abs_root}\")")
        
    except OSError as e:
        print(f"[Error] Failed to create skill structure: {e}")
        sys.exit(1)
        
if __name__ == "__main__":
    main()
