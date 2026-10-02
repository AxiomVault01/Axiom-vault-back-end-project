class FileParser:

    @staticmethod
    def parse(file):
        # Imported here, not at the top: pandas (with numpy) adds tens of MB to every web
        # process, and it is only needed when a payroll file is actually uploaded.
        import pandas as pd

        name = file.name.lower()

        if name.endswith(".csv"):
            df = pd.read_csv(file)

        elif name.endswith(".xlsx"):
            df = pd.read_excel(file)

        else:
            raise ValueError("Unsupported file format")

        return df.to_dict(orient="records")