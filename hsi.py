"""
Hang Seng Index Constituents Scraper
------------------------------------
Fetches the current list of Hang Seng Index constituent stocks from the
Chinese Wikipedia page and saves snapshots / history / changes.

Output files (all written to the script's own directory):
    hangsengindex_constituents.csv        Current snapshot, schema:
                                    ticker, company, industry, date
    hangsengindex_historical_components.csv   Daily rolling history of ticker strings
    hangsengindex_changes.csv             Added / removed tickers per day
    README.md                        Human-readable change log
"""

import os
from datetime import date, datetime
import pandas as pd
import requests
import io

# ----------------------------------------------------------------------
# Set working directory to the script's location so relative paths always
# resolve correctly, regardless of where the script is invoked from.
# ----------------------------------------------------------------------
os.chdir(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------

def create_constituents(df):
    """Create a one-row DataFrame with today's comma-separated ticker string."""
    ticker_list = df['ticker'].tolist()
    res_string = ','.join(ticker_list)

    results_df = pd.DataFrame({
        'date': [date.today()],
        'tickers': [res_string],
    })
    return results_df


def diff_tickers(historical):
    """Compare consecutive daily snapshots and return added/removed tickers."""
    added_tickers = {}
    removed_tickers = {}

    for i in range(1, len(historical.index)):
        prev_tickers = set(historical.iloc[i - 1].tickers)
        current_tickers = set(historical.iloc[i].tickers)

        added = current_tickers - prev_tickers
        if added:
            added_tickers[historical.index[i]] = sorted(list(added))

        removed = prev_tickers - current_tickers
        if removed:
            removed_tickers[historical.index[i]] = sorted(list(removed))

    at_only = pd.DataFrame(added_tickers.items(), columns=['date', 'added_tickers'])
    rt_only = pd.DataFrame(removed_tickers.items(), columns=['date', 'removed_tickers'])
    combined = pd.merge(at_only, rt_only, on='date', how='outer')
    combined = combined.sort_values('date', ascending=True).set_index('date')

    return combined


def find_constituent_table(tables):
    """
    Locate the Hang Seng Index constituent table among all wikitable tables.
    Identification heuristic: the table must contain a ticker-code column
    (代號 / 代碼) AND a name column (名稱 / 公司).
    Returns the matching DataFrame, or None if not found.
    """
    for tbl in tables:
        cols = [str(c) for c in tbl.columns]
        joined = ' '.join(cols)
        has_code = any(k in joined for k in ('代號', '代碼', '編號'))
        has_name = any(k in joined for k in ('名稱', '公司', '證券'))
        if has_code and has_name:
            return tbl
    return None


def map_columns(target_df):
    """
    Map the page's Chinese column names to the standard schema.
    The HSI table has: 股份代號 / 名稱 / 流通系數（%） / 比重（%）
    We keep all 4 source columns + date.
    """
    col_map = {}

    for col in target_df.columns:
        if col in ('股份代號', '股份代碼', '代號', '代碼', '股票代號', '編號'):
            col_map['ticker'] = col
        elif col in ('名稱', '公司', '證券名稱', '股份名稱'):
            col_map['company'] = col
        elif '流通' in col or '系數' in col or '系数' in col:
            col_map['freefloat_pct'] = col
        elif '比重' in col or '權重' in col or '权重' in col or '占比' in col:
            col_map['weight_pct'] = col

    # Substring fallback for ticker / company
    if 'ticker' not in col_map:
        for col in target_df.columns:
            if '代號' in col or '代碼' in col or '編號' in col:
                col_map['ticker'] = col
                break
    if 'company' not in col_map:
        for col in target_df.columns:
            if '名稱' in col or '公司' in col:
                col_map['company'] = col
                break

    missing = [k for k in ('ticker', 'company') if k not in col_map]
    if missing:
        raise KeyError(
            f"Could not map required columns: {missing}. "
            f"Available: {list(target_df.columns)}"
        )
    return col_map

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    import re
    from bs4 import BeautifulSoup

    header = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }

    hist_filename = 'hangsengindex_historical_components.csv'
    if os.path.exists(hist_filename):
        hangseng_hist = pd.read_csv(hist_filename)
    else:
        hangseng_hist = pd.DataFrame(columns=['date', 'tickers'])

    # ---- Fetch ----
    url = 'https://zh.wikipedia.org/wiki/%E6%81%92%E7%94%9F%E6%8C%87%E6%95%B8'
    print(f"Fetching: {url}")
    r = requests.get(url, headers=header, timeout=30)
    r.encoding = 'utf-8'
    if r.status_code != 200:
        raise RuntimeError(f"HTTP status: {r.status_code}")

    # ---- Use BeautifulSoup to locate the EXACT constituent table ----
    soup = BeautifulSoup(r.text, 'html.parser')

    target_table = None
    for tbl in soup.find_all('table', class_='wikitable'):
        # Collect all header text to identify the constituent table
        headers = [th.get_text(strip=True) for th in tbl.find_all('th')]
        joined = ' '.join(headers)
        has_code = any(k in joined for k in ('代號', '代碼', '編號'))
        has_name = any(k in joined for k in ('名稱', '公司', '證券'))
        if has_code and has_name:
            target_table = tbl
            break

    if target_table is None:
        raise ValueError("Constituent table not found on the page.")

    # ---- Sanitize colspan/rowspan BEFORE feeding to pandas ----
    # Some Wikipedia pages contain garbage like colspan="3`" which breaks
    # pandas' strict int() conversion.
    attr_regex = re.compile(r'^(\d+)[^\d]*$')   # '3`' -> '3'
    for tag in target_table.find_all(['td', 'th']):
        for attr in ('colspan', 'rowspan'):
            val = tag.get(attr)
            if val is not None:
                m = attr_regex.match(str(val).strip())
                if m:
                    tag[attr] = m.group(1)
                else:
                    del tag[attr]   # drop truly unparseable ones

    # ---- Now parse ONLY the cleaned table ----
    html_stream = io.StringIO(str(target_table))
    target_df = pd.read_html(html_stream, header=0)[0]

    # Normalize column names
    target_df.columns = (
        target_df.columns
        .astype(str)
        .str.replace(r'\[.*\]', '', regex=True)
        .str.strip()
    )
    print("Discovered columns:", list(target_df.columns))

    col_map = map_columns(target_df)

    # Build standardized DataFrame. freefloat_pct / weight_pct are optional.
    keep_cols = [col_map['ticker'], col_map['company']]
    col_names = ['ticker', 'company']
    if 'freefloat_pct' in col_map:
        keep_cols.append(col_map['freefloat_pct'])
        col_names.append('freefloat_pct')
    if 'weight_pct' in col_map:
        keep_cols.append(col_map['weight_pct'])
        col_names.append('weight_pct')

    constituents = target_df[keep_cols].copy()
    constituents.columns = col_names
    constituents['date'] = date.today()

    # Coerce percentage columns to numeric, non-numeric -> NaN
    for pct_col in ('freefloat_pct', 'weight_pct'):
        if pct_col in constituents.columns:
            constituents[pct_col] = pd.to_numeric(
                constituents[pct_col].astype(str).str.replace('%', '', regex=False).str.strip(),
                errors='coerce'
            )

    constituents.dropna(subset=['ticker'], inplace=True)
    constituents['ticker'] = (
        constituents['ticker']
        .astype(str)
        .str.replace(r'\.0$', '', regex=True)
        .str.strip()
    )

    # Drop header-repeat rows that pandas.read_html sometimes creates
    constituents = constituents[constituents['ticker'] != '代號']
    constituents = constituents[constituents['ticker'] != '股份代號']

    # ---- NEW: drop summary/total rows (共 / 總 / 合计 / 平均 etc.) ----
    # HSI ticker codes are purely numeric (e.g. 00001, 00700). Any row whose
    # ticker contains non-digit characters is a summary row, not a stock.
    constituents = constituents[
        constituents['ticker'].str.fullmatch(r'\d+', na=False)
    ].copy()
    constituents['ticker'] = constituents['ticker'].str.zfill(5)
    constituents.sort_values(by='ticker', ascending=True, inplace=True)
    constituents.reset_index(drop=True, inplace=True)

    constituents.to_csv('hangsengindex_constituents.csv', index=False,
                       encoding='utf-8-sig')
    print(f"Saved {len(constituents)} constituents to hangsengindex_constituents.csv")

    df_history = create_constituents(constituents)
    df_history['date'] = pd.to_datetime(df_history['date']).dt.strftime("%Y-%m-%d")
    final = pd.concat([hangseng_hist, df_history], ignore_index=True)
    final = final.drop_duplicates(subset=['date', 'tickers'], keep='last')
    final.to_csv(hist_filename, index=False)
    print(f"Updated history: {hist_filename} ({len(final)} rows)")

    hist = pd.read_csv(hist_filename, index_col='date')
    hist['tickers'] = hist['tickers'].apply(lambda x: sorted(str(x).split(',')))
    hist = hist.sort_index()
    combined = diff_tickers(hist)
    combined.to_csv('hangsengindex_changes.csv')
    print("Saved hangsengindex_changes.csv")

    if not combined.empty:
        changes_readme = combined.iloc[-50:].sort_index(ascending=False)
        markdown_table = changes_readme.to_markdown()
    else:
        markdown_table = "No historical variations recorded yet."

    readme_path = "README.md"
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="UTF-8") as f:
            f.write("\n" * 10)

    with open(readme_path, "r+", encoding="UTF-8") as f:
        lines = f.readlines()[:10]
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines += ['\n',
              f'Hang Seng Index Constituents Auto Renew at **{current_datetime}**',
              '\n\n',
              markdown_table,
              '\n']
    with open(readme_path, "w+", encoding="UTF-8") as f:
        f.writelines(lines)

    print("Done. README.md updated.")

if __name__ == '__main__':
    main()