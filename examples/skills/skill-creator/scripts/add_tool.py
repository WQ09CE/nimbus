
import argparse
import sys
from pathlib import Path
import yaml

def main():
    parser = argparse.ArgumentParser(description="Add a tool to a Nimbus Skill")
    parser.add_argument("--skill_path", required=True, help="Path to the skill directory")
    parser.add_argument("--tool_name", required=True, help="Tool name")
    parser.add_argument("--tool_description", required=True, help="Tool description")
    parser.add_argument("--script_name", required=True, help="Entrypoint script name")
    parser.add_argument("--args", help="Arguments in format 'name:type,name2:type'")
    
    args = parser.parse_args()
    
    skill_dir = Path(args.skill_path)
    if not skill_dir.exists():
        print(f"[Error] Skill directory '{skill_dir}' not found.")
        sys.exit(1)
        
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        print(f"[Error] SKILL.md not found in {skill_dir}")
        sys.exit(1)
        
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists():
        print(f"[Error] scripts/ directory not found in {skill_dir}")
        scripts_dir.mkdir(exist_ok=True)
        # Create it if not exists, but warn
    
    # Parse existing manifest
    try:
        content = skill_file.read_text("utf-8")
        parts = content.split("---", 2)
        if len(parts) < 3:
            print("[Error] Invalid SKILL.md format (missing frontmatter)")
            sys.exit(1)
            
        manifest = yaml.safe_load(parts[1])
        markdown_body = parts[2]
        
    except (yaml.YAMLError, OSError) as e:
        print(f"[Error] Failed to read SKILL.md: {e}")
        sys.exit(1)
        
    # Check if tool exists
    existing_tools = manifest.get("tools", [])
    if any(t["name"] == args.tool_name for t in existing_tools):
        print(f"[Error] Tool '{args.tool_name}' already exists.")
        sys.exit(1)
        
    # Construct args dict
    tool_args = {}
    if args.args:
        for arg in args.args.split(","):
            parts = arg.split(":")
            if len(parts) == 2:
                arg_name, arg_type = parts[0].strip(), parts[1].strip()
                if arg_type.lower() == "int":
                    arg_type = "integer"
                if arg_type.lower() == "bool":
                    arg_type = "boolean"
                if arg_type.lower() == "str":
                    arg_type = "string"
                    
                tool_args[arg_name] = {
                    "type": arg_type,
                    "description": f"The {arg_name} argument"
                }
            else:
                 print(f"[Warning] Skipping malformed arg definition: {arg}")
                 
    new_tool = {
        "name": args.tool_name,
        "description": args.tool_description,
        "entrypoint": f"scripts/{args.script_name}",
        "args": tool_args
    }
    
    # Update manifest
    if "tools" not in manifest:
        manifest["tools"] = []
    manifest["tools"].append(new_tool)
    
    # Write back manifest
    try:
        with open(skill_file, "w") as f:
            f.write("---\n")
            yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)
            f.write("---\n")
            f.write(markdown_body)
    except OSError as e:
        print(f"[Error] Failed to write SKILL.md: {e}")
        sys.exit(1)
        
    # Generate template script
    script_path = scripts_dir / args.script_name
    if not script_path.exists():
        try:
            with open(script_path, "w") as f:
                f.write("#!/usr/bin/env python3\n")
                f.write("import argparse\n")
                f.write("import sys\n\n")
                f.write("def main():\n")
                f.write(f'    parser = argparse.ArgumentParser(description="{args.tool_description}")\n')
                
                # Add args to template
                for arg_name, arg_spec in tool_args.items():
                    flags = f'--{arg_name}'
                    type_hint = ""
                    action = ""
                    
                    if arg_spec["type"] == "integer":
                        type_hint = ", type=int"
                    elif arg_spec["type"] == "boolean":
                        action = ', action="store_true"'
                        
                    f.write(f'    parser.add_argument("{flags}"{type_hint}{action}, help="{arg_spec["description"]}")\n')
                    
                f.write("\n    args = parser.parse_args()\n\n")
                f.write(f'    print(f"[Info] Executing {args.tool_name}...")\n')
                f.write("    # TODO: Implement tool logic here\n")
                if tool_args:
                    f.write("    print(f'Args: {vars(args)}')\n")
                else:
                    f.write("    print('No args provided')\n")
                    
                f.write("\nif __name__ == '__main__':\n")
                f.write("    main()\n")
                
            script_path.chmod(0o755) # Make executable
            
            print(f"[Success] Added tool '{args.tool_name}' to SKILL.md")
            print(f"[Success] Created script template at {script_path}")
            
        except OSError as e:
             print(f"[Warning] Failed to create script template: {e}")
    else:
        print(f"[Success] Added tool '{args.tool_name}' to SKILL.md (script already exists at {script_path})")
        
if __name__ == "__main__":
    main()
