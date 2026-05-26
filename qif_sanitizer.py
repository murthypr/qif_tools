"""
QIF Sanitizer Tool

This module provides functionality to parse QIF (Quicken Interchange Format) files,
replace Quicken categories with GnuCash account names, and write sanitized QIF files.

The tool reads category mappings from a configuration file and applies them to
transaction records, leaving unmapped categories unchanged.

Supports both single-file and directory-based batch processing:
- Single-file mode: Process one QIF file specified as a command-line argument
- Directory mode: Automatically scan INPUT_DIR from config and process all .qif/.QIF files
"""

import os
import re
import time
from pathlib import Path


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


def get_qif_files(input_dir):
    """
    Scan a directory for all QIF files (.qif and .QIF extensions).
    
    This function searches the specified directory for files ending in .qif or .QIF
    and returns them in sorted order (case-insensitive, with .QIF before .qif).
    
    Args:
        input_dir (str): Path to the directory to scan.
        
    Returns:
        list: A sorted list of absolute paths to QIF files found in the directory.
              Returns an empty list if no QIF files are found.
        
    Raises:
        FileNotFoundError: If the input directory does not exist.
        NotADirectoryError: If the input path is not a directory.
    """
    input_path = Path(input_dir)
    
    if not input_path.exists():
        raise FileNotFoundError(f"Input directory '{input_dir}' does not exist.")
    
    if not input_path.is_dir():
        raise NotADirectoryError(f"'{input_dir}' is not a directory.")
    
    # Find all files ending with .qif or .QIF (case-insensitive)
    qif_files = []
    for file_path in input_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() == '.qif':
            qif_files.append(str(file_path.absolute()))
    
    # Sort files for consistent processing order
    return sorted(qif_files)


def process_file(input_path, output_path, mappings):
    """
    Process a single QIF file: load, sanitize, and write output.
    
    This function encapsulates the core sanitization logic for a single file:
    1. Load the QIF file
    2. Apply category mappings
    3. Write the sanitized content to the output file (overwriting if it exists)
    4. Return per-file statistics for reporting and aggregation
    
    Args:
        input_path (str): Absolute path to the input QIF file.
        output_path (str): Absolute path to the output file to write.
        mappings (dict): Dictionary mapping Quicken categories to GnuCash account names.
        
    Returns:
        dict: Statistics about the processing, including:
            - 'transactions_processed': Number of transactions processed in this file
            - 'category_replacements': Total number of category replacements made
            - 'memo_tags_added': Number of memo tags created or updated
            - 'replacement_details': Dictionary with per-category replacement counts
            
    Raises:
        FileNotFoundError: If the input file is not found.
        IOError: If the output file cannot be written.
    """
    # Load QIF file
    qif_content = load_qif_file(input_path)
    
    # Apply mappings and collect per-file statistics
    sanitized_content, replacement_counts, tag_insert_count, transactions_processed = apply_mappings_to_qif(qif_content, mappings)
    
    # Write sanitized QIF to output location
    write_sanitized_qif(sanitized_content, output_path)
    
    # Calculate total category replacements for this file
    total_replacements = sum(replacement_counts.values())
    
    return {
        'transactions_processed': transactions_processed,
        'category_replacements': total_replacements,
        'memo_tags_added': tag_insert_count,
        'replacement_details': replacement_counts
    }





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
    
    In QIF format, transactions are terminated by a line containing only '^'.
    This function gathers lines until '^' and returns each transaction as a list
    of lines without the terminator.
    
    Args:
        qif_content (str): The raw content of a QIF file.
        
    Returns:
        list: A list of transactions, where each transaction is a list of lines.
    """
    lines = qif_content.splitlines()
    transactions = []
    current_transaction = []
    
    for line in lines:
        if line.strip() == '^':
            transactions.append(current_transaction)
            current_transaction = []
        else:
            current_transaction.append(line)
    
    # Keep any trailing transaction without a terminator
    if current_transaction:
        transactions.append(current_transaction)
    
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


def sanitize_tag(tag):
    """
    Normalize a tag for use in memo lines.

    Normalization rules applied in order:
    1. Replace all whitespace characters (spaces, tabs, newlines) with underscores
    2. Convert to TitleCase (capitalize first letter of each word after underscore)
    3. Remove all non-alphanumeric characters except underscores

    This ensures tags are safe for use in memo fields and have a consistent format.
    Tags are normalized to contain only letters, numbers, and underscores.

    Example transformations:
        "Lake Oswego" → "Lake_Oswego"
        "Prius 05" → "Prius_05"
        "US" → "Us"
        "San Francisco/Bay Area" → "San_Francisco_Bay_Area"

    Args:
        tag (str): The raw tag string from a category or Government field
        
    Returns:
        str: The sanitized tag suitable for memo prefixes (e.g., "#Lake_Oswego")
    """
    if not tag:
        return tag

    # Step 1: Replace all whitespace characters with underscores
    sanitized = re.sub(r'\s+', '_', tag)

    # Step 2: Convert to TitleCase by capitalizing first letter of each word (separated by _)
    words = sanitized.split('_')
    words = [word.capitalize() if word else '' for word in words]
    sanitized = '_'.join(words)

    # Step 3: Keep only alphanumeric characters and underscores
    # Remove any special characters (/, -, &, etc.) that might cause issues
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)

    return sanitized


def process_category_tags(transaction_lines):
    """
    Extract tags from category lines and move them to memo lines.

    This function processes category lines (L or S) that contain a slash (/).
    The part before the slash is the category; the part after is the tag.
    The tag is moved to the beginning of the memo line (or a new memo is created).

    Example:
        Input:  LRent/Lake Oswego
        Output: LRent with "#Lake Oswego " prepended to memo

    Args:
        transaction_lines (list[str]): One transaction's lines, excluding the terminator.

    Returns:
        tuple: A tuple of (processed_lines, tag_updated) where:
            - processed_lines (list[str]): Processed transaction lines.
            - tag_updated (bool): True if a tag was extracted and memo was updated.
    """
    processed_lines = []
    extracted_tag = None
    tag_updated = False

    # FIRST PASS: Extract tags from category lines
    for line in transaction_lines:
        if line and line[0] in {'L', 'S'}:
            prefix = line[0]
            category_text = line[1:]

            # Check if category contains a slash (tag separator)
            if '/' in category_text:
                parts = category_text.split('/', 1)
                category = parts[0].strip()
                tag = parts[1].strip()

                # Rewrite the line with only the category
                line = prefix + category

                # Remember the tag for memo processing (use first found)
                if not extracted_tag and tag:
                    extracted_tag = tag

        processed_lines.append(line)

    # SECOND PASS: Handle memo updates for extracted tags
    if extracted_tag:
        # Search for an existing memo line (starts with 'M')
        memo_line_index = None
        for index, line in enumerate(processed_lines):
            if line and line.startswith('M'):
                memo_line_index = index
                break

        if memo_line_index is not None:
            # Existing memo found: prepend the sanitized tag if not already present
            existing_memo = processed_lines[memo_line_index][1:]
            sanitized_tag = sanitize_tag(extracted_tag)
            if not existing_memo.startswith(f"#{sanitized_tag}"):
                processed_lines[memo_line_index] = 'M' + f"#{sanitized_tag} " + existing_memo
                tag_updated = True
        else:
            # No existing memo: create a new one with the sanitized tag
            sanitized_tag = sanitize_tag(extracted_tag)
            processed_lines.append('M' + f"#{sanitized_tag}")
            tag_updated = True

    return processed_lines, tag_updated


def apply_mappings_to_transaction(transaction_lines, mappings, replacement_counts):
    """
    Process one full QIF transaction at a time.

    The transaction is represented as a list of lines ending before the '^'
    terminator. This function performs the following actions in order:
    1. Extract tags from category lines (format: Category/Tag) and move to memo
    2. Detect category lines starting with 'L' or 'S'.
    3. If a category is `Government:<Country>`:
        - replace it with `Expenses:Government`
        - extract `<Country>` for memo prefixing
        - increment replacement counts for the original category
    4. Apply normal category mapping from the mappings file for non-Government categories.
    5. If a Government category was found, update the memo line even when the memo
       appears before the category line.

    Args:
        transaction_lines (list[str]): One transaction's lines, excluding the terminator.
        mappings (dict): Dictionary mapping Quicken categories to GnuCash account names.
        replacement_counts (dict): Dictionary to track replacements per Quicken category.

    Returns:
        tuple: A tuple of (processed_lines, memo_updated) where:
            - processed_lines (list[str]): Processed transaction lines with '^' terminator.
            - memo_updated (bool): True if a memo line was created or modified.
    """
    # STEP 1: Process category tags (split Category/Tag format)
    processed_lines, tag_updated = process_category_tags(transaction_lines)

    # STEP 2: Process category mappings and Government countries
    processed_lines_final = []
    government_country = None
    memo_updated = tag_updated

    # FIRST PASS: Scan all lines and replace category codes while collecting Government country info.
    # We do this in a separate pass so we know the government_country before we process the memo line,
    # which allows the memo line to appear anywhere in the transaction (before or after the category).
    for line in processed_lines:
        if line and line[0] in {'L', 'S'}:
            # Extract the prefix (L or S) and the category text
            prefix = line[0]
            category = line[1:]

            # Special case: Government:<Country> format
            if category.startswith('Government:'):
                # Split "Government:US" into "Government" and "US"
                parts = category.split(':', 1)
                country = parts[1].strip() if len(parts) > 1 else ''
                
                # Always map Government categories to the unified account
                line = prefix + 'Expenses:Government'
                
                # Track this replacement by original category name (e.g., "Government:US")
                replacement_counts[category] = replacement_counts.get(category, 0) + 1
                
                # Remember the country code for memo prefixing (use first found)
                if not government_country and country:
                    government_country = country

            # Normal mapping: look up the category in the mappings dictionary
            elif category in mappings:
                line = prefix + mappings[category]
                replacement_counts[category] = replacement_counts.get(category, 0) + 1

        processed_lines_final.append(line)

    # SECOND PASS: Handle memo updates for Government categories.
    # Now that we know if this transaction had a Government category and the country code,
    # we can update or create the memo line with the country prefix.
    if government_country:
        # Search for an existing memo line (starts with 'M')
        memo_line_index = None
        for index, line in enumerate(processed_lines_final):
            if line and line.startswith('M'):
                memo_line_index = index
                break

        if memo_line_index is not None:
            # Existing memo found: prepend the sanitized country code if not already present
            existing_memo = processed_lines_final[memo_line_index][1:]
            sanitized_country = sanitize_tag(government_country)
            if not existing_memo.startswith(f"#{sanitized_country}"):
                processed_lines_final[memo_line_index] = 'M' + f"#{sanitized_country} " + existing_memo
                memo_updated = True
        else:
            # No existing memo: create a new one with the sanitized country code
            sanitized_country = sanitize_tag(government_country)
            processed_lines_final.append('M' + f"#{sanitized_country}")
            memo_updated = True

    return processed_lines_final, memo_updated


def apply_mappings_to_qif(qif_content, mappings):
    """
    Apply category mappings to all transactions in QIF content.
    
    The QIF file is processed one transaction at a time. Transactions are
    split on lines containing only '^'. Each transaction is processed as a unit
    and then reassembled with the '^' terminator.
    
    This function also tracks per-file counters for:
      - transactions processed
      - category replacements
      - memo tags added

    Args:
        qif_content (str): The raw content of a QIF file.
        mappings (dict): Dictionary mapping Quicken categories to GnuCash account names.
        
    Returns:
        tuple: A tuple containing:
            - QIF content with all categories mapped
            - replacement counts dictionary
            - total memo updates count (used as tag_insert_count for reporting)
            - transactions processed count
    """
    transactions = split_qif_transactions(qif_content)
    replacement_counts = {}
    # tag_insert_count: Tracks the number of tags moved to memos
    # This includes tags from Category/Tag format (e.g., Auto:Fuel/Prius 05 -> #Prius 05)
    # and Government country tags (e.g., Government:US -> #US)
    tag_insert_count = 0
    processed_transactions = []
    transactions_processed = 0
    
    for txn in transactions:
        # Count each transaction. This counter resets for each file and is returned per-file.
        transactions_processed += 1
        processed_lines, memo_updated = apply_mappings_to_transaction(txn, mappings, replacement_counts)
        processed_transactions.append(processed_lines)
        if memo_updated:
            tag_insert_count += 1
    
    result = '\n^\n'.join('\n'.join(txn) for txn in processed_transactions)
    if result:
        result += '\n^'
    
    # Return the tag_insert_count and transaction count for reporting
    return result, replacement_counts, tag_insert_count, transactions_processed


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
    return f"{name}-sanitized-v2.QIF"


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


def main(input_qif_file=None):
    """
    Main function to sanitize QIF files.
    
    This function supports two operating modes:
    
    1. Directory Mode (batch processing):
       - Automatically activated if INPUT_DIR is set in qif_sanitizer.config
       - Scans INPUT_DIR for all .qif/.QIF files
       - Processes each file in sorted order
       - Writes sanitized files to OUTPUT_DIR
       - Progress messages show: "N out of M: Processing <filename>..."
       - Input file argument is ignored in this mode
    
    2. Single-File Mode (legacy behavior):
       - Activated if INPUT_DIR is empty in config
       - Processes the file specified in input_qif_file argument
       - Writes output to the same directory as the input file
       - Generates output filename with "-sanitized-v2" suffix
    
    Args:
        input_qif_file (str, optional): Path to the input QIF file for single-file mode.
                                        Ignored if INPUT_DIR is set in config.
        
    Returns:
        str or list: In single-file mode, returns path to the output file.
                     In directory mode, returns list of output file paths.
        
    Raises:
        FileNotFoundError: If input file, config, or mappings files are not found.
        ValueError: If configuration is invalid.
    """
    # Load configuration
    config = load_config()
    mappings_file = config['MAPPINGS_FILE']
    input_dir = config.get('INPUT_DIR', '').strip()
    output_dir = config.get('OUTPUT_DIR', '').strip()
    
    # Read mappings (once for all files)
    print(f"Loading mappings from: {mappings_file}")
    mappings = read_mappings_file(mappings_file)
    print(f"Loaded {len(mappings)} category mappings")
    
    # Check if directory mode is enabled
    if input_dir:
        # ===== DIRECTORY MODE (Batch Processing) =====
        # Scan INPUT_DIR for all QIF files and process them in sorted order
        try:
            qif_files = get_qif_files(input_dir)
        except (FileNotFoundError, NotADirectoryError) as e:
            print(f"Error scanning directory: {e}")
            return []
        
        file_count = len(qif_files)
        
        if file_count == 0:
            print(f"No QIF files found in {input_dir}")
            return []
        
        # Progress message: report how many files will be processed
        print(f"\nFound {file_count} files. Will be processing them...")
        print()
        
        output_paths = []
        total_transactions = 0
        total_category_replacements = 0
        total_memo_tags_added = 0
        start_time = time.perf_counter()
        
        # Process each file in sorted order
        for file_index, input_path in enumerate(qif_files, 1):
            input_filename = os.path.basename(input_path)
            
            # Progress message: starting a file
            print(f"{file_index} out of {file_count}: Processing {input_filename}...")
            
            try:
                # Generate output filename using OUTPUT_DIR
                output_filename = generate_output_filename(input_path)
                
                if output_dir:
                    # Use specified OUTPUT_DIR
                    output_path = os.path.join(output_dir, output_filename)
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                else:
                    # Fall back to input directory if OUTPUT_DIR is not set
                    output_path = os.path.join(input_dir, output_filename)
                
                # Process the file and collect per-file statistics
                stats = process_file(input_path, output_path, mappings)
                output_paths.append(output_path)
                
                total_transactions += stats['transactions_processed']
                total_category_replacements += stats['category_replacements']
                total_memo_tags_added += stats['memo_tags_added']
                
                # Progress message: completed a file
                print(f"{file_index} out of {file_count}: Completed processing {input_filename}")
                print(f"Stats for {input_filename}:")
                print(f"  Transactions processed: {stats['transactions_processed']}")
                print(f"  Category replacements: {stats['category_replacements']}")
                print(f"  Memo tags added: {stats['memo_tags_added']}")
                print()
                
            except Exception as e:
                print(f"{file_index} out of {file_count}: ERROR processing {input_filename}: {e}")
                print()
                continue
        
        end_time = time.perf_counter()
        elapsed_seconds = end_time - start_time
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"Directory batch processing completed")
        print(f"Processed {len(output_paths)} out of {file_count} files")
        print(f"Overall totals:")
        print(f"  Total transactions processed: {total_transactions}")
        print(f"  Total category replacements: {total_category_replacements}")
        print(f"  Total memo tags added: {total_memo_tags_added}")
        
        elapsed_minutes = int(elapsed_seconds // 60)
        elapsed_secs = int(elapsed_seconds % 60)
        if elapsed_minutes > 0:
            print(f"Total processing time: {elapsed_minutes} min and {elapsed_secs} secs")
        else:
            print(f"Total processing time: {elapsed_secs} secs")
        
        print(f"Output directory: {output_dir if output_dir else input_dir}")
        print(f"{'='*60}\n")
        
        return output_paths
    else:
        # ===== SINGLE-FILE MODE (Legacy Behavior) =====
        if not input_qif_file:
            raise ValueError("No input file specified and INPUT_DIR is not configured")
        
        output_filename = generate_output_filename(input_qif_file)
        output_dir = os.path.dirname(input_qif_file) or '.'
        output_path = os.path.join(output_dir, output_filename)
        
        print(f"Processing single file: {input_qif_file}")
        start_time = time.perf_counter()
        stats = process_file(input_qif_file, output_path, mappings)
        end_time = time.perf_counter()
        elapsed_seconds = end_time - start_time
        
        # Print per-file summary
        print(f"{os.path.basename(input_qif_file)} processing complete")
        print(f"Stats for {os.path.basename(input_qif_file)}:")
        print(f"  Transactions processed: {stats['transactions_processed']}")
        print(f"  Category replacements: {stats['category_replacements']}")
        print(f"  Memo tags added: {stats['memo_tags_added']}")
        
        replacement_counts = stats['replacement_details']
        if replacement_counts:
            print("\nReplacement summary:")
            for quicken_category, count in sorted(replacement_counts.items()):
                if count > 0:
                    gnucash_account = mappings.get(quicken_category, "<unknown>")
                    print(f"{quicken_category}: found and replaced {count} instances with {gnucash_account}")
            print(f"\nTotal replacements across all categories: {stats['category_replacements']}")
            print(f"Moved {stats['memo_tags_added']} tags to memos.")
        else:
            print("No mapped category replacements were found.")
        print("End of Replacement summary.\n")
        
        elapsed_minutes = int(elapsed_seconds // 60)
        elapsed_secs = int(elapsed_seconds % 60)
        if elapsed_minutes > 0:
            time_message = f"took {elapsed_minutes} min and {elapsed_secs} secs to process the file {os.path.basename(input_qif_file)}."
        else:
            time_message = f"took {elapsed_secs} secs to process the file {os.path.basename(input_qif_file)}."
        
        print(time_message)
        print(f"Successfully sanitized QIF file")
        print(f"Output file: {output_path}")
        print(f"Overall totals:")
        print(f"  Total transactions processed: {stats['transactions_processed']}")
        print(f"  Total category replacements: {stats['category_replacements']}")
        print(f"  Total memo tags added: {stats['memo_tags_added']}")
        
        return output_path


if __name__ == "__main__":
    import sys
    
    # Check if an input file was provided as command-line argument
    if len(sys.argv) >= 2:
        # Single-file mode: process the specified file
        input_file = sys.argv[1]
        main(input_file)
    else:
        # Check if directory mode is configured
        try:
            config = load_config()
            input_dir = config.get('INPUT_DIR', '').strip()
            if input_dir:
                # Directory mode is configured: run batch processing
                main()
            else:
                # No input file and no directory configured: show usage
                print("Usage: python qif_sanitizer.py <input_qif_file>")
                print("   or configure INPUT_DIR in qif_sanitizer.config for batch mode")
                print("\nExample (single-file mode):")
                print("  python qif_sanitizer.py transactions.qif")
                print("\nExample (directory mode - configure in qif_sanitizer.config):")
                print("  INPUT_DIR = \"/path/to/qif/files\"")
                print("  OUTPUT_DIR = \"/path/to/output/files\"")
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
