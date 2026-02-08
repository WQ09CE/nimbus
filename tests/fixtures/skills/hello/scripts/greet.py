import sys
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="World")
    parser.add_argument("--loud", action="store_true")
    args = parser.parse_args()

    # Note: Nimbus arguments are passed as --name value --loud True
    # If using store_true, we need to handle "True" string properly or just use value
    # But ScriptTool passes --loud True or just skips false.
    # Standard argparse handles --loud (no value).
    # ScriptTool implementation: "if v is True: append(f'--{k}')"
    # So if loud=True, it passes --loud.
    # If loud=False, it passes nothing.
    
    msg = f"Hello, {args.name}!"
    if args.loud:
        msg = msg.upper()
        
    print(msg)

if __name__ == "__main__":
    main()
