import json
from collections import defaultdict
import requests

# แหล่ง all-in-one
URL = "https://raw.githubusercontent.com/thailand-geography-data/thailand-geography-json/main/src/geography.json"

def main():
    data = requests.get(URL, timeout=60).json()

    # โครงสร้างที่เหมาะกับ dropdown แบบพึ่งพา:
    # provinces -> districts -> subdistricts (+ postalCode)
    provinces = {}  # {provinceCode: {nameTh, nameEn, districts:{districtCode:{...}}}}

    for row in data:
        pcode = str(row["provinceCode"]).zfill(2)
        dcode = str(row["districtCode"]).zfill(4)
        scode = str(row["subdistrictCode"]).zfill(6)

        p = provinces.setdefault(pcode, {
            "provinceCode": pcode,
            "provinceNameTh": row["provinceNameTh"],
            "provinceNameEn": row["provinceNameEn"],
            "districts": {}
        })

        d = p["districts"].setdefault(dcode, {
            "districtCode": dcode,
            "districtNameTh": row["districtNameTh"],
            "districtNameEn": row["districtNameEn"],
            "subdistricts": []
        })

        d["subdistricts"].append({
            "subdistrictCode": scode,
            "subdistrictNameTh": row["subdistrictNameTh"],
            "subdistrictNameEn": row["subdistrictNameEn"],
            "postalCode": str(row["postalCode"]).zfill(5) if row.get("postalCode") else ""
        })

    # แปลงให้เป็น list + sort เพื่อใช้งานง่าย
    result = []
    for pcode in sorted(provinces.keys()):
        p = provinces[pcode]
        districts_list = []
        for dcode in sorted(p["districts"].keys()):
            d = p["districts"][dcode]
            d["subdistricts"].sort(key=lambda x: x["subdistrictNameTh"])
            districts_list.append(d)
        p["districts"] = districts_list
        result.append(p)

    with open("th_address.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    print("✅ done -> th_address.json")

if __name__ == "__main__":
    main()
