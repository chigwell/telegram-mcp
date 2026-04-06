import ast
import sys
import os
from pprint import pprint

def validate_shadowing(filepath: str):
    """
    Parses a python file and ensures no local variable (or argument) 
    in any function shadows a top-level module import.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()
    
    tree = ast.parse(source, filename=filepath)
    
    # 1. Collect all top-level imports
    global_imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                global_imports.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                global_imports.add(alias.asname or alias.name)

    print(f"[{os.path.basename(filepath)}] Tracked top-level imports: {len(global_imports)}")

    shadow_errors = []

    # 2. Traverse all functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = node.name
            
            # 2a. Check arguments
            args = []
            if node.args.args: args.extend(node.args.args)
            if node.args.posonlyargs: args.extend(node.args.posonlyargs)
            if node.args.kwonlyargs: args.extend(node.args.kwonlyargs)
            if node.args.vararg: args.append(node.args.vararg)
            if node.args.kwarg: args.append(node.args.kwarg)

            for arg in args:
                if arg.arg in global_imports:
                    shadow_errors.append(f"Function '{func_name}' has parameter '{arg.arg}' shadowing an import.")

            # 2b. Check local assignments (any Name with Store context)
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    if child.id in global_imports:
                        shadow_errors.append(f"Function '{func_name}' has local variable '{child.id}' shadowing an import. Line {getattr(child, 'lineno', '?')}")

    if shadow_errors:
        print(f"\n[CRITICAL FATAL] Shadowing detected in {filepath}:")
        for err in shadow_errors:
            print(f"  - {err}")
        return False
    
    print(f"[{os.path.basename(filepath)}] AST Shadowing Check: PASSED \u2705")
    return True

if __name__ == "__main__":
    files_to_check = [
        "src/telegram_mcp/mcp_server.py",
        "src/telegram_mcp/client.py"
    ]
    
    all_passed = True
    for f in files_to_check:
        full_path = os.path.join(os.getcwd(), f)
        if os.path.exists(full_path):
            if not validate_shadowing(full_path):
                all_passed = False
        else:
            print(f"Warning: File {f} not found.")
            
    if not all_passed:
        sys.exit(1)
    
    sys.exit(0)
