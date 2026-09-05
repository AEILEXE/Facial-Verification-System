"""
Quezon City barangay reference data for FANS-C.

Used by BeneficiaryInfoForm and BeneficiaryEditForm to populate the
barangay dropdown when city = "Quezon City", and to validate that the
submitted barangay is a real QC barangay.

Source: Quezon City official barangay list (142 barangays, 6 districts).
"""

QC_CITY = "Quezon City"
QC_REGION = "Metro Manila (NCR)"

QC_DISTRICT_BARANGAYS = {
    "District 1": [
        "Alicia", "Bagong Pag-asa", "Bahay Toro", "Balingasa", "Bungad",
        "Damar", "Damayan", "Del Monte", "Katipunan", "Lourdes", "Maharlika",
        "Manresa", "Mariblo", "Masambong", "N.S. Amoranto", "Nayong Kanluran",
        "Paang Bundok", "Pag-ibig sa Nayon", "Paltok", "Paraiso", "Phil-am",
        "Project 6", "Ramon Magsaysay", "Salvacion", "San Antonio",
        "San Isidro Labrador", "San Jose", "Siena", "St. Peter", "Sta. Cruz",
        "Sta. Teresita", "Sto. Cristo", "Sto. Domingo", "Talayan", "Vasra",
        "Veterans Village", "West Triangle",
    ],
    "District 2": [
        "Bagong Silangan", "Batasan Hills", "Commonwealth", "Holy Spirit", "Payatas",
    ],
    "District 3": [
        "Amihan", "Bagumbayan", "Bagumbuhay", "Bayanihan", "Blue Ridge A",
        "Blue Ridge B", "Camp Aguinaldo", "Dioquino Zobel", "Duyan-Duyan",
        "E. Rodriguez", "East Kamias", "Escopa I", "Escopa II", "Escopa III",
        "Escopa IV", "Libis", "Loyola Heights", "Mangga", "Marilag", "Masagana",
        "Matandang Balara", "Milagrosa", "Pansol", "Quirino 2-A", "Quirino 2-B",
        "Quirino 2-C", "Quirino 3-A", "Quirino 3-B (Claro)", "San Roque",
        "Silangan", "Socorro", "St. Ignatius", "Tagumpay", "Ugong Norte",
        "Villa Maria Clara", "West Kamias", "White Plains",
    ],
    "District 4": [
        "Bagong Lipunan ng Crame", "Botocan", "Central", "Damayang Lagi",
        "Don Manuel", "Doña Aurora", "Doña Imelda", "Doña Josefa", "Horseshoe",
        "Immaculate Concepcion", "Kalusugan", "Kamuning", "Kaunlaran",
        "Kristong Hari", "Krus na Ligas", "Laging Handa", "Malaya", "Mariana",
        "Obrero", "Old Capitol Site", "Paligsahan", "Pinagkaisahan", "Pinyahan",
        "Roxas", "Sacred Heart", "San Isidro Galas", "San Martin de Porres",
        "San Vicente", "Santol", "Sikatuna Village", "South Triangle", "Sto. Niño",
        "Tatalon", "Teachers' Village East", "Teachers' Village West",
        "UP Campus", "UP Village", "Valencia",
    ],
    "District 5": [
        "Bagbag", "Capri", "Fairview", "Greater Lagro", "Gulod",
        "Kaligayahan", "Nagkaisang Nayon", "North Fairview", "Novaliches Proper",
        "Pasong Putik Proper", "San Agustin", "San Bartolome", "Sta. Lucia",
        "Sta. Monica",
    ],
    "District 6": [
        "Apolonio Samson", "Baesa", "Balon Bato", "Culiat", "New Era",
        "Pasong Tamo", "Sangandaan", "Sauyo", "Talipapa", "Tandang Sora",
        "Unang Sigaw",
    ],
}

QC_BARANGAYS: list[str] = []
for _district_barangays in QC_DISTRICT_BARANGAYS.values():
    QC_BARANGAYS.extend(_district_barangays)

QC_BARANGAY_SET: frozenset[str] = frozenset(QC_BARANGAYS)

# Choices for the barangay select when city = Quezon City.
# Flat list (used for backend validation and simple select widgets).
QC_BARANGAY_CHOICES = [('', '-- Select Barangay --')] + [
    (b, b) for b in QC_BARANGAYS
]

# Grouped choices for optgroup rendering: list of (group_label, [(value, label), ...])
QC_BARANGAY_OPTGROUP_CHOICES = [
    (district, [(b, b) for b in barangays])
    for district, barangays in QC_DISTRICT_BARANGAYS.items()
]
