/**
 * Cascading address dropdowns for the beneficiary registration form.
 *
 * Province  -> Municipality/City (select or text)
 * Municipality -> Barangay (select for QC, text for others)
 *
 * API endpoints (requires login):
 *   GET /dashboard/api/municipalities/?province=<name>
 *   GET /dashboard/api/barangays/?municipality=<name>
 */

(function () {
  const provinceSelect = document.getElementById('id_province');
  const municipalityWrap = document.getElementById('municipality_wrap');
  const barangayWrap = document.getElementById('barangay_wrap');

  if (!provinceSelect) return;

  const municipalityInput = document.getElementById('id_municipality');
  const barangayInput = document.getElementById('id_barangay');

  // Preserve any existing values (e.g. on validation failure redisplay)
  const savedMunicipality = municipalityInput ? municipalityInput.value : '';
  const savedBarangay = barangayInput ? barangayInput.value : '';

  provinceSelect.addEventListener('change', function () {
    const province = this.value;
    resetMunicipality();
    resetBarangay();
    if (!province) return;
    loadMunicipalities(province);
  });

  function loadMunicipalities(province) {
    fetch(`/dashboard/api/municipalities/?province=${encodeURIComponent(province)}`)
      .then(r => r.json())
      .then(data => {
        const cities = data.municipalities || [];
        if (cities.length > 0) {
          replaceMunicipalityWithSelect(cities);
        } else {
          showMunicipalityText('Enter municipality / city...');
        }
      })
      .catch(() => {
        showMunicipalityText('Enter municipality / city...');
      });
  }

  function replaceMunicipalityWithSelect(cities) {
    const select = createSelect('id_municipality', 'municipality', cities, savedMunicipality);
    select.addEventListener('change', function () {
      resetBarangay();
      if (this.value) loadBarangays(this.value);
    });
    replaceInputWithSelect(municipalityWrap, select);

    // Trigger barangay load if there was a saved value
    if (savedMunicipality && cities.includes(savedMunicipality)) {
      select.value = savedMunicipality;
      loadBarangays(savedMunicipality);
    }
  }

  function showMunicipalityText(placeholder) {
    const inp = createTextInput('id_municipality', 'municipality', placeholder);
    replaceSelectWithInput(municipalityWrap, inp);
  }

  function loadBarangays(municipality) {
    fetch(`/dashboard/api/barangays/?municipality=${encodeURIComponent(municipality)}`)
      .then(r => r.json())
      .then(data => {
        const barangays = data.barangays || [];
        if (barangays.length > 0) {
          replaceBarangayWithSelect(barangays);
        } else {
          showBarangayText('Enter barangay name...');
        }
      })
      .catch(() => {
        showBarangayText('Enter barangay name...');
      });
  }

  function replaceBarangayWithSelect(barangays) {
    const select = createSelect('id_barangay', 'barangay', barangays, savedBarangay);
    replaceInputWithSelect(barangayWrap, select);
  }

  function showBarangayText(placeholder) {
    const inp = createTextInput('id_barangay', 'barangay', placeholder);
    replaceSelectWithInput(barangayWrap, inp);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function createSelect(id, name, options, selected) {
    const sel = document.createElement('select');
    sel.id = id;
    sel.name = name;
    sel.className = 'form-select';
    sel.required = true;

    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '-- Select --';
    sel.appendChild(blank);

    options.forEach(opt => {
      const o = document.createElement('option');
      o.value = opt;
      o.textContent = opt;
      if (opt === selected) o.selected = true;
      sel.appendChild(o);
    });
    return sel;
  }

  function createTextInput(id, name, placeholder) {
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.id = id;
    inp.name = name;
    inp.className = 'form-control';
    inp.placeholder = placeholder;
    inp.required = true;
    return inp;
  }

  function replaceInputWithSelect(wrap, select) {
    if (!wrap) return;
    const existing = wrap.querySelector('input, select');
    if (existing) wrap.replaceChild(select, existing);
    else wrap.appendChild(select);
  }

  function replaceSelectWithInput(wrap, input) {
    if (!wrap) return;
    const existing = wrap.querySelector('input, select');
    if (existing) wrap.replaceChild(input, existing);
    else wrap.appendChild(input);
  }

  function resetMunicipality() {
    const inp = createTextInput('id_municipality', 'municipality', 'Select province first...');
    inp.disabled = false;
    replaceInputWithSelect(municipalityWrap, inp);
  }

  function resetBarangay() {
    const inp = createTextInput('id_barangay', 'barangay', 'Enter barangay name...');
    replaceInputWithSelect(barangayWrap, inp);
  }

  // Trigger on page load if province already selected (e.g. form redisplay)
  if (provinceSelect.value) {
    loadMunicipalities(provinceSelect.value);
  }
})();
