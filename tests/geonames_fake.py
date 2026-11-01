"""GeoNames' three files in miniature (invented places), for the tests: nothing is fetched from the internet."""
import io
import zipfile

CITIES = "\n".join([
    "1\tHarbourton\tHarbourton\t\t5.1053\t-1.2466\tP\tPPLA\tGH\t\t09\t\t\t\t90000\t\t10\tUTC\t2020-01-01",
    "2\tNorthfield\tNorthfield\t\t51.5072\t-0.1276\tP\tPPLC\tGB\t\tENG\t\t\t\t8000000\t\t10\tEurope/London\t2020-01-01"])
COUNTRIES = "#header\nGH\tGHA\t288\tGH\tGhanaland\tAccra\t1\nGB\tGBR\t826\tUK\tBritannia\tLondon\t1"
ADMIN1 = "GH.09\tCoastal Region\tCoastal Region\t1\nGB.ENG\tGreater North\tGreater North\t1"


def fetch(url: str) -> bytes:
    name = url.rsplit("/", 1)[1]
    if name == "cities1000.zip":
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("cities1000.txt", CITIES)
        return out.getvalue()
    return {"countryInfo.txt": COUNTRIES, "admin1CodesASCII.txt": ADMIN1}[name].encode()
