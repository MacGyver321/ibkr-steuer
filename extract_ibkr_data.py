import xml.etree.ElementTree as ET
import csv
import os
import sys

FX_FIELDS = ['date', 'settleDate', 'currency', 'fxRateToBase', 'activityCode',
              'activityDescription', 'amount', 'debit', 'credit', 'balance',
              'transactionID', 'levelOfDetail', 'assetCategory', 'symbol',
              'buySell', 'tradeQuantity', 'tradePrice', 'tradeGross',
              'tradeCommission']


def extract_fx_from_root(root, base_curr, fx_fields=None):
    """Extract FX transactions from a parsed XML root element."""
    if fx_fields is None:
        fx_fields = FX_FIELDS
    stmtfunds_node = root.find('.//StmtFunds')
    if stmtfunds_node is None:
        return []
    fx_rows = []
    for row in stmtfunds_node:
        attrib = row.attrib
        if attrib.get('levelOfDetail') != 'Currency':
            continue
        if attrib.get('currency') == base_curr:
            continue
        record = {k: attrib.get(k, '') for k in fx_fields}
        fx_rows.append(record)
    return fx_rows


def extract_fx_multi_xml(xml_files, output_dir):
    """Extract and merge FX transactions from multiple XML files (multi-year).

    The main XML (last file) is used for all standard sections (trades, funds, etc.).
    FX transactions are merged from ALL files for complete FIFO lot history.
    """
    if not xml_files:
        return

    # Sort by filename to ensure chronological order
    xml_files = sorted(xml_files)
    main_xml = xml_files[-1]  # Last file = tax year

    print(f"Multi-XML: {len(xml_files)} Dateien, Haupt-XML: {os.path.basename(main_xml)}")

    # 1. Parse main XML normally (all standard sections)
    parse_ibkr_xml(main_xml, output_dir)

    # 2. Detect base currency from main XML
    tree = ET.parse(main_xml)
    root = tree.getroot()
    acct = root.find('.//AccountInformation')
    base_curr = acct.attrib.get('currency', 'EUR') if acct is not None else 'EUR'

    # 3. Merge FX transactions from ALL XMLs
    all_fx = []
    for xml_path in xml_files:
        try:
            t = ET.parse(xml_path)
            r = t.getroot()
            rows = extract_fx_from_root(r, base_curr)
            # Tag the source file for debugging
            from_date = ''
            stmt = r.find('.//FlexStatement')
            if stmt is not None:
                from_date = stmt.attrib.get('fromDate', '')
            print(f"  {os.path.basename(xml_path)}: {len(rows)} FX-Einträge (ab {from_date})")
            all_fx.extend(rows)
        except Exception as e:
            print(f"  FEHLER bei {xml_path}: {e}")

    # Sort chronologically and deduplicate by transactionID
    all_fx.sort(key=lambda x: x.get('date', ''))
    seen_ids = set()
    deduped = []
    for row in all_fx:
        tid = row.get('transactionID', '')
        desc = row.get('activityDescription', '')
        if desc == 'Starting Balance':
            # Only keep the earliest Starting Balance per currency
            key = ('SB', row.get('currency', ''), row.get('date', ''))
        elif tid:
            key = tid
        else:
            key = (row.get('date'), row.get('currency'), row.get('amount'))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        deduped.append(row)

    # Only keep the EARLIEST Starting Balance per currency (from earliest XML)
    sb_seen = set()
    final_rows = []
    for row in deduped:
        if row.get('activityDescription') == 'Starting Balance':
            curr = row.get('currency', '')
            if curr in sb_seen:
                continue
            sb_seen.add(curr)
        final_rows.append(row)

    # Write merged FX transactions
    fx_path = os.path.join(output_dir, 'fx_transactions.csv')
    sorted_h = sorted(FX_FIELDS)
    with open(fx_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=sorted_h)
        writer.writeheader()
        writer.writerows(final_rows)
    print(f"Saved {len(final_rows)} merged FX transactions to {fx_path} (aus {len(xml_files)} XMLs)")


def parse_ibkr_xml(xml_file_path, output_dir):
    print(f"Parsing {xml_file_path}...")
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing XML: {e}")
        return

    # Define sections to extract
    # Mapping: XML Tag -> Output Filename
    sections = {
        'Trades': 'trades.csv',
        'CashTransactions': 'cash_transactions.csv',
        'CorporateActions': 'corporate_actions.csv',
        'SecuritiesInfo': 'financial_instruments.csv',
        'StmtFunds': 'statement_of_funds.csv',
        'FIFOPerformanceSummaryInBase': 'pnl_summary.csv'
    }
    
    for section_tag, filename in sections.items():
        section_node = root.find(f'.//{section_tag}')
        
        if section_node is None:
            print(f"Section <{section_tag}> not found. Skipping.")
            continue
            
        print(f"Processing section: {section_tag}")
        
        # Get all children (rows)
        rows = list(section_node)
        if not rows:
            print(f"No rows found in {section_tag}")
            continue
            
        # Collect headers from all keys in all rows to handle optional attributes
        headers = set()
        data_rows = []
        
        for row in rows:
            attrib = row.attrib
            headers.update(attrib.keys())
            # Extract ALL records
            record = attrib.copy()
            record['__source_section__'] = section_tag
            
            data_rows.append(record)


        # Write to CSV
        output_path = os.path.join(output_dir, filename)
        # Sort headers for consistency
        headers.add('__source_section__')
        sorted_headers = sorted(list(headers))
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=sorted_headers)
            writer.writeheader()
            writer.writerows(data_rows)
            
        print(f"Saved {len(data_rows)} rows to {output_path}")

    # Extract FX transactions from StmtFunds (levelOfDetail="Currency", non-base currency)
    # These are needed for FIFO-based foreign currency gain/loss calculation
    acct_info_node = root.find('.//AccountInformation')
    base_curr = acct_info_node.attrib.get('currency', 'EUR') if acct_info_node is not None else 'EUR'

    fx_fields = ['date', 'settleDate', 'currency', 'fxRateToBase', 'activityCode',
                  'activityDescription', 'amount', 'debit', 'credit', 'balance',
                  'transactionID', 'levelOfDetail', 'assetCategory', 'symbol',
                  'buySell', 'tradeQuantity', 'tradePrice', 'tradeGross',
                  'tradeCommission']

    fx_rows = extract_fx_from_root(root, base_curr, fx_fields)

    if fx_rows:
        fx_path = os.path.join(output_dir, 'fx_transactions.csv')
        sorted_h = sorted(fx_fields)
        with open(fx_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=sorted_h)
            writer.writeheader()
            writer.writerows(fx_rows)
        print(f"Saved {len(fx_rows)} FX transactions to {fx_path}")

    # Extract MTM Performance Summary for CASH (FX positions) — plausibility reference
    mtm_section = root.find('.//MTMPerformanceSummaryInBase')
    if mtm_section is not None:
        mtm_rows = []
        mtm_headers = set()
        for row in mtm_section:
            attrib = row.attrib
            if attrib.get('assetCategory') != 'CASH':
                continue
            if attrib.get('symbol') == base_curr:
                continue
            record = attrib.copy()
            mtm_headers.update(record.keys())
            mtm_rows.append(record)

        if mtm_rows:
            mtm_path = os.path.join(output_dir, 'fx_mtm_summary.csv')
            sorted_h = sorted(mtm_headers)
            with open(mtm_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=sorted_h)
                writer.writeheader()
                writer.writerows(mtm_rows)
            print(f"Saved {len(mtm_rows)} FX MTM summary rows to {mtm_path}")

    # Extract fxTranslationGainLoss from CashReportCurrency (IBKR's own FX PnL calc)
    cash_report = root.find('.//CashReportCurrency[@levelOfDetail="BaseCurrency"][@currency="BASE_SUMMARY"]')
    if cash_report is None:
        # Try iterating
        for cr in root.iter('CashReportCurrency'):
            if cr.attrib.get('currency') == 'BASE_SUMMARY' and cr.attrib.get('levelOfDetail') == 'BaseCurrency':
                cash_report = cr
                break
    if cash_report is not None:
        fx_tgl = cash_report.attrib.get('fxTranslationGainLoss', '0')
        fx_tgl_path = os.path.join(output_dir, 'fx_translation.csv')
        with open(fx_tgl_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['fxTranslationGainLoss'])
            writer.writeheader()
            writer.writerow({'fxTranslationGainLoss': fx_tgl})
        print(f"Saved fxTranslationGainLoss: {fx_tgl} to {fx_tgl_path}")

    # Extract AccountInformation (single element with base currency)
    acct_info = acct_info_node
    if acct_info is not None:
        acct_data = acct_info.attrib.copy()
        acct_path = os.path.join(output_dir, 'account_info.csv')
        with open(acct_path, 'w', newline='', encoding='utf-8') as f:
            headers = sorted(acct_data.keys())
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerow(acct_data)
        print(f"Saved account info (base currency: {acct_data.get('currency', '?')}) to {acct_path}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        xml_file = sys.argv[1]
        output_dir = sys.argv[2]
    else:
        xml_file = "input.xml"
        output_dir = "./"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Check for --fx-history flag: additional XMLs for FX lot history
    fx_history_files = []
    remaining_args = sys.argv[3:]
    if '--fx-history' in remaining_args:
        idx = remaining_args.index('--fx-history')
        fx_history_files = remaining_args[idx + 1:]

    if fx_history_files:
        # Multi-XML mode: main XML + history files
        all_xmls = fx_history_files + [xml_file]
        extract_fx_multi_xml(all_xmls, output_dir)
    else:
        parse_ibkr_xml(xml_file, output_dir)
