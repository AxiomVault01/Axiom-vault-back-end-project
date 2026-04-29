import pandas as pd


class FileParser:

    @staticmethod
    def parse(file):
        name = file.name.lower()

        if name.endswith(".csv"):
            df = pd.read_csv(file)

        elif name.endswith(".xlsx"):
            df = pd.read_excel(file)

        else:
            raise ValueError("Unsupported file format")

        return df.to_dict(orient="records")