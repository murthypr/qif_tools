"""
QIF Sanitizer Tool

This module provides functionality to parse QIF (Quicken Interchange Format) files,
replace Quicken categories with GnuCash account names, and write sanitized QIF files.

The tool reads category mappings from a configuration file and applies them to
transaction records, leaving unmapped categories unchanged.
"""

import os
import time


def load_config(config_file="qif_sanitizer.config"):
    """
    Load configuration from the config file.
    
    Args:
        config_file (str): Path to the configuration file.
        
    Returns:
        dict: Dictionary containing configuration variables.
        
    Raises:
        FileNotFoundError: If the config file is not found.
        ValueError: If required configuration variables are missing.
    """
    config = {}
    
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file '{config_file}' not found.")
    
    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse key = value pairs
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                config[key] = value
    
    if 'MAPPINGS_FILE' not in config:
        raise ValueError("Configuration must include 'MAPPINGS_FILE' variable.")
    
    return config


def load_qif_file(qif_file):
    """
    Load a QIF file into memory.
    
    Args:
        qif_file (str): Path to the QIF file to load.
        
    Returns:
        str: The contents of the QIF file.
        
    Raises:
        FileNotFoundError: If the QIF file is not found.
    """
    if not os.path.exists(qif_file):
        raise FileNotFoundError(f"QIF file '{qif_file}' not found.")
    
    with open(qif_file, 'r', encoding='utf-8') as f:
        return f.read()


def split_qif_transactions(qif_content):
    """
    Split QIF content into individual transactions.
    
    In QIF format, transactions are separated by '!' characters. Each transaction
    is a sequence of lines until the next '!' delimiter.
    
    Args:
        qif_content (str): The raw content of a QIF file.
        
    Returns:
        list: A list of transaction strings, each representing a complete transaction.
    """
    # Split by '!' delimiter but preserve the delimiters in context
    lines = qif_content.split('\n')
    transactions = []
    current_transaction = []
    
    for line in lines:
        if line.strip() == '!':
            if current_transaction:
                # Join the transaction lines and add to list
                transactions.append('\n'.join(current_transaction))
                current_transaction = []
        else:
            current_transaction.append(line)
    
    # Don't forget the last transaction if file doesn't end with '!'
    if current_transaction:
        transactions.append('\n'.join(current_transaction))
    
    return transactions


def read_mappings_file(mappings_file):
    """
    Read category mappings from a file.
    
    The mappings file should contain lines in the format:
        QuickenCategory = GnuCashAccountName
    
    Blank lines and lines starting with '#' are ignored.
    
    Args:
        mappings_file (str): Path to the mappings file.
        
    Returns:
        dict: A dictionary mapping Quicken categories to GnuCash account names.
        
    Raises:
        FileNotFoundError: If the mappings file is not found.
    """
    mappings = {}
    
    if not os.path.exists(mappings_file):
        raise FileNotFoundError(f"Mappings file '{mappings_file}' not found.")
    
    with open(mappings_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse mapping lines: QuickenCategory = GnuCashAccountName
            if '=' in line:
                quicken_cat, gnucash_acct = line.split('=', 1)
                quicken_cat = quicken_cat.strip()
                gnucash_acct = gnucash_acct.strip()
                mappings[quicken_cat] = gnucash_acct
    
    return mappings



def apply_mappings_to_transaction(transaction, mappings, replacement_counts):
    """
    Apply category mappings to a single transaction.

    New behavior:
    - If a category line (starting with `L` or `S`) matches the pattern
      `Government:<Country>` it is mapped to the single GnuCash account
      `Expenses:Government` and the transaction memo is prefixed with
      `#<Country>` (or a new memo line `M#<Country>` is created if none exists).
    - This Government handling runs BEFORE the normal mapping lookup.
    - For all other categories, mappings from the mappings file are applied as before.

    The function updates `replacement_counts` for each Quicken category that
    was replaced (including Government entries, keyed by the original Quicken
    category string such as `Government:US`).

    Args:
        transaction (str): A single transaction as a string (lines separated by newlines).
        mappings (dict): Dictionary mapping Quicken categories to GnuCash account names.
        replacement_counts (dict): Dictionary to track replacements per Quicken category.

    Returns:
        str: The transaction with categories and (if applicable) memo updates applied.
    """
    lines = transaction.split('\n')
    result_lines = []

    # Track if a Government category was found for this transaction and the country
    government_country = None
    memo_applied = False

    for line in lines:
        # Process category lines that begin with 'L' or 'S'
        if line and line[0] in {'L', 'S'}:
            prefix = line[0]
            category = line[1:]

            # Special handling: Government:<Country>
            if category.startswith('Government:'):
                # Extract country after the colon (allow whitespace)
                parts = category.split(':', 1)
                country = parts[1].strip() if len(parts) > 1 else ''

                # Map to the single GnuCash account
                line = prefix + 'Expenses:Government'

                # Record replacement count keyed by the original Quicken category
                replacement_counts[category] = replacement_counts.get(category, 0) + 1

                # Remember the first government country to prefix the memo
                if not government_country and country:
                    government_country = country

            # Normal mapping lookup (only if not Government)
            elif category in mappings:
                line = prefix + mappings[category]
                replacement_counts[category] = replacement_counts.get(category, 0) + 1

        # Memo line handling: if we have a government prefix to apply,
        # insert it at the beginning of the memo text. Preserve existing memo.
        if line and line.startswith('M') and government_country and not memo_applied:
            existing_memo = line[1:]
            line = 'M' + f"#{government_country}" + existing_memo
            memo_applied = True

        result_lines.append(line)

    # If a government country was found but no memo line existed, create one
    if government_country and not memo_applied:
        result_lines.append('M' + f"#{government_country}")

    return '\n'.join(result_lines)


def apply_mappings_to_qif(qif_content, mappings):
    """
    Apply category mappings to all transactions in QIF content.
    
    Args:
        qif_content (str): The raw QIF file content.
        mappings (dict): Dictionary mapping Quicken categories to GnuCash account names.
        
    Returns:
        tuple: A tuple containing the QIF content with all categories mapped and
               the replacement counts dictionary.
    """
    transactions = split_qif_transactions(qif_content)
    replacement_counts = {}
    mapped_transactions = [
        apply_mappings_to_transaction(txn, mappings, replacement_counts)
        for txn in transactions
    ]
    
    # Reconstruct the QIF file with '!' delimiters between transactions
    # and a final '!' at the end
    result = '\n!\n'.join(mapped_transactions)
    if result:
        result += '\n!'
    
    return result, replacement_counts


def generate_output_filename(input_file):
    """
    Generate the output filename based on the input filename.
    
    The output filename follows the pattern:
        <input_filename>-sanitized-v1.QIF
    
    For example:
        transactions.qif -> transactions-sanitized-v1.QIF
    
    Args:
        input_file (str): Path to the input QIF file.
        
    Returns:
        str: The generated output filename (without directory path).
    """
    base_name = os.path.basename(input_file)
    name, ext = os.path.splitext(base_name)
    return f"{name}-sanitized-v1.QIF"


def write_sanitized_qif(qif_content, output_file):
    """
    Write sanitized QIF content to a file.
    
    Args:
        qif_content (str): The sanitized QIF content to write.
        output_file (str): Path to the output file.
        
    Raises:
        IOError: If the file cannot be written.
    """
    # Ensure the output directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(qif_content)


def main(input_qif_file):
    """
    Main function to sanitize a QIF file.
    
    This function orchestrates the sanitization process:
    1. Loads the configuration file
    2. Reads the category mappings
    3. Loads the input QIF file
    4. Applies category mappings to all transactions
    5. Generates the output filename
    6. Writes the sanitized QIF to disk
    
    Args:
        input_qif_file (str): Path to the input QIF file to sanitize.
        
    Returns:
        str: Path to the generated output file.
        
    Raises:
        FileNotFoundError: If input file or config/mappings files are not found.
        ValueError: If configuration is invalid.
    """
    # Load configuration
    config = load_config()
    mappings_file = config['MAPPINGS_FILE']
    
    # Read mappings
    print(f"Loading mappings from: {mappings_file}")
    mappings = read_mappings_file(mappings_file)
    print(f"Loaded {len(mappings)} category mappings")
    
    # Load QIF file
    print(f"Loading QIF file: {input_qif_file}")
    qif_content = load_qif_file(input_qif_file)
    print(f"QIF file loaded ({len(qif_content)} bytes)")
    
    # Apply mappings
    print("Applying category mappings...")
    start_time = time.perf_counter()
    sanitized_content, replacement_counts = apply_mappings_to_qif(qif_content, mappings)
    end_time = time.perf_counter()
    elapsed_seconds = end_time - start_time
    
    # Print replacement summary
    if replacement_counts:
        print("\n\nReplacement summary:")
        for quicken_category, count in sorted(replacement_counts.items()):
            if count > 0:
                gnucash_account = mappings.get(quicken_category, "<unknown>")
                print(f"{quicken_category}: found and replaced {count} instances with {gnucash_account}")
    else:
        print("No mapped category replacements were found.")
    print("End of Replacement summary.\n\n")
    
    # Generate output filename and write file
    output_filename = generate_output_filename(input_qif_file)
    output_dir = os.path.dirname(input_qif_file) or '.'
    output_path = os.path.join(output_dir, output_filename)
    
    print(f"Writing sanitized QIF to: {output_path}")
    write_sanitized_qif(sanitized_content, output_path)
    
    elapsed_minutes = int(elapsed_seconds // 60)
    elapsed_secs = int(elapsed_seconds % 60)
    if elapsed_minutes > 0:
        time_message = f"took {elapsed_minutes} min and {elapsed_secs} secs to process the file {os.path.basename(input_qif_file)}."
    else:
        time_message = f"took {elapsed_secs} secs to process the file {os.path.basename(input_qif_file)}."
    
    print(time_message)
    print(f"Successfully sanitized QIF file")
    print(f"Output file: {output_path}")
    
    return output_path


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python qif_sanitizer.py <input_qif_file>")
        print("Example: python qif_sanitizer.py transactions.qif")
        sys.exit(1)
    
    input_file = sys.argv[1]
    main(input_file)
