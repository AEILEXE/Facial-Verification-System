/**
 * QC Address UI — FANS-C
 *
 * Handles city/barangay cascading:
 *   City = Quezon City → barangay select with 142 QC barangays (grouped by district)
 *   City = Others      → barangay free-text input
 *   City = blank       → barangay disabled placeholder
 *
 * No server API calls; the full barangay list is embedded here.
 * autocomplete="off" is set on all address fields to suppress browser autofill.
 */

(function () {
  'use strict';

  /* ── Complete QC barangay list grouped by district ─────────────────────── */
  const QC_DISTRICTS = {
    'District 1': [
      'Alicia','Bagong Pag-asa','Bahay Toro','Balingasa','Bungad',
      'Damar','Damayan','Del Monte','Katipunan','Lourdes','Maharlika',
      'Manresa','Mariblo','Masambong','N.S. Amoranto','Nayong Kanluran',
      'Paang Bundok','Pag-ibig sa Nayon','Paltok','Paraiso','Phil-am',
      'Project 6','Ramon Magsaysay','Salvacion','San Antonio',
      'San Isidro Labrador','San Jose','Siena','St. Peter','Sta. Cruz',
      'Sta. Teresita','Sto. Cristo','Sto. Domingo','Talayan','Vasra',
      'Veterans Village','West Triangle'
    ],
    'District 2': [
      'Bagong Silangan','Batasan Hills','Commonwealth','Holy Spirit','Payatas'
    ],
    'District 3': [
      'Amihan','Bagumbayan','Bagumbuhay','Bayanihan','Blue Ridge A',
      'Blue Ridge B','Camp Aguinaldo','Dioquino Zobel','Duyan-Duyan',
      'E. Rodriguez','East Kamias','Escopa I','Escopa II','Escopa III',
      'Escopa IV','Libis','Loyola Heights','Mangga','Marilag','Masagana',
      'Matandang Balara','Milagrosa','Pansol','Quirino 2-A','Quirino 2-B',
      'Quirino 2-C','Quirino 3-A','Quirino 3-B (Claro)','San Roque',
      'Silangan','Socorro','St. Ignatius','Tagumpay','Ugong Norte',
      'Villa Maria Clara','West Kamias','White Plains'
    ],
    'District 4': [
      'Bagong Lipunan ng Crame','Botocan','Central','Damayang Lagi',
      'Don Manuel','Doña Aurora','Doña Imelda','Doña Josefa','Horseshoe',
      'Immaculate Concepcion','Kalusugan','Kamuning','Kaunlaran',
      'Kristong Hari','Krus na Ligas','Laging Handa','Malaya','Mariana',
      'Obrero','Old Capitol Site','Paligsahan','Pinagkaisahan','Pinyahan',
      'Roxas','Sacred Heart','San Isidro Galas','San Martin de Porres',
      'San Vicente','Santol','Sikatuna Village','South Triangle','Sto. Niño',
      'Tatalon',"Teachers' Village East","Teachers' Village West",
      'UP Campus','UP Village','Valencia'
    ],
    'District 5': [
      'Bagbag','Capri','Fairview','Greater Lagro','Gulod',
      'Kaligayahan','Nagkaisang Nayon','North Fairview','Novaliches Proper',
      'Pasong Putik Proper','San Agustin','San Bartolome','Sta. Lucia',
      'Sta. Monica'
    ],
    'District 6': [
      'Apolonio Samson','Baesa','Balon Bato','Culiat','New Era',
      'Pasong Tamo','Sangandaan','Sauyo','Talipapa','Tandang Sora',
      'Unang Sigaw'
    ]
  };

  /* ── DOM references ─────────────────────────────────────────────────────── */
  const citySelect    = document.getElementById('id_municipality');
  const barangayWrap  = document.getElementById('barangay_wrap');

  if (!citySelect || !barangayWrap) return;  // widget not present on this page

  const savedBarangay = (barangayWrap.querySelector('input,select') || {}).value || '';

  /* ── Helpers ────────────────────────────────────────────────────────────── */

  function buildQCSelect(selectedValue) {
    const sel = document.createElement('select');
    sel.id        = 'id_barangay';
    sel.name      = 'barangay';
    sel.className = 'form-select';
    sel.required  = true;
    sel.setAttribute('autocomplete', 'off');

    const blank = document.createElement('option');
    blank.value       = '';
    blank.textContent = '-- Select Barangay --';
    if (!selectedValue) blank.selected = true;
    sel.appendChild(blank);

    Object.entries(QC_DISTRICTS).forEach(([district, barangays]) => {
      const grp = document.createElement('optgroup');
      grp.label = district;
      barangays.forEach(b => {
        const opt = document.createElement('option');
        opt.value       = b;
        opt.textContent = b;
        if (b === selectedValue) opt.selected = true;
        grp.appendChild(opt);
      });
      sel.appendChild(grp);
    });
    return sel;
  }

  function buildTextInput(placeholder, currentValue) {
    const inp = document.createElement('input');
    inp.type        = 'text';
    inp.id          = 'id_barangay';
    inp.name        = 'barangay';
    inp.className   = 'form-control';
    inp.required    = true;
    inp.placeholder = placeholder;
    inp.value       = currentValue || '';
    inp.setAttribute('autocomplete', 'off');
    return inp;
  }

  function buildDisabledInput() {
    const inp = buildTextInput('Select a city first', '');
    inp.disabled = true;
    inp.required = false;
    return inp;
  }

  function replaceBarangay(newEl) {
    const existing = barangayWrap.querySelector('input,select');
    if (existing) {
      barangayWrap.replaceChild(newEl, existing);
    } else {
      barangayWrap.appendChild(newEl);
    }
  }

  /* ── City-change handler ────────────────────────────────────────────────── */

  function onCityChange(city, savedValue) {
    if (city === 'Quezon City') {
      replaceBarangay(buildQCSelect(savedValue));
    } else if (city === 'Others') {
      replaceBarangay(buildTextInput('Type barangay name...', ''));
      const hint = document.getElementById('barangay_others_hint');
      if (hint) hint.style.display = '';
    } else {
      replaceBarangay(buildDisabledInput());
    }
    // Hide the "Others" hint for non-Others cities
    if (city !== 'Others') {
      const hint = document.getElementById('barangay_others_hint');
      if (hint) hint.style.display = 'none';
    }
  }

  citySelect.addEventListener('change', function () {
    onCityChange(this.value, '');  // reset barangay on city change
  });

  /* ── Page-load initialisation ───────────────────────────────────────────── */
  // Apply the correct barangay widget immediately based on current city value.
  onCityChange(citySelect.value, savedBarangay);

})();
