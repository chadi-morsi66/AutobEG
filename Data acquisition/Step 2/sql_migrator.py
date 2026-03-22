import pandas as pd
from sqlalchemy import create_engine

# 1. Load your existing CSV
df = pd.read_csv("/root/AutobEG/Data acquisition/Data/step2_listings.csv")

# 2. Connect to your new Cloud Database
# Format: postgresql://user:password@localhost:5432/database_name
engine = create_engine('postgresql://scraper_user:your_strong_password@localhost:5432/autob_eg')

# 3. Push the data (This creates the table automatically!)
df.to_sql('cars', engine, if_exists='replace', index=False)

print("Data successfully moved to PostgreSQL!")