import pandas as pd

df = pd.read_parquet("data/clean/year=2016/month=09/violations.parquet")
print(df.dtypes)
print(df.head())
print(df["ticket_number"].is_unique, df["ticket_number"].duplicated().sum())