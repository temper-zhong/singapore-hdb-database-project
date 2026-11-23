"""Create a Q4 validation database from the included processed CSV snapshots."""

from pathlib import Path
import argparse
import sqlite3

import pandas as pd


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root / 'tmp/q4_validation.sqlite')
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to modify an existing database.
    with output.open('xb'):
        pass
    data_dir = root / 'data/03_processed_data'
    tables = ('dim_town', 'dim_flat_type', 'dim_time_month', 'fact_resale_transaction')
    with sqlite3.connect(output) as connection:
        for name in tables:
            frame = pd.read_csv(data_dir / f'{name}.csv')
            for column in frame.columns:
                if column.endswith('_flag'):
                    values = frame[column].astype(str).str.lower()
                    if not values.isin(['true', 'false', '1', '0']).all():
                        raise ValueError(f'Unexpected flag in {name}.{column}')
                    frame[column] = values.isin(['true', '1']).astype(int)
            frame.to_sql(name, connection, if_exists='fail', index=False, chunksize=2000)
            print(f'{name}: {len(frame):,} rows')
        connection.execute('CREATE INDEX ix_q4_month ON fact_resale_transaction(month_id)')
        connection.execute('CREATE INDEX ix_q4_quarter ON fact_resale_transaction(quarter_id)')
    print(f'Created: {output}')


if __name__ == '__main__':
    main()
