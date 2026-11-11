// Shared confirmation dialog for deleting a whole week of shifts or a whole month of
// field-duty records. Both are bulk, irreversible operations, so each one requires
// typing the exact phrase shown in the dialog.
let rangeClearSubmit = null;

function openRangeClear({title, phrase, body, submit, onDone}) {
  $('rangeClearTitle').textContent = title;
  $('rangeClearBody').textContent = body;
  $('rangeClearPhrase').textContent = phrase;
  const input = $('rangeClearForm').elements.confirm;
  input.value = '';
  input.placeholder = phrase;
  $('rangeClearStatus').textContent = '';
  rangeClearSubmit = {phrase, submit, onDone};
  $('rangeClearDialog').showModal();
}

$('closeRangeClear').onclick = () => $('rangeClearDialog').close();

$('rangeClearForm').onsubmit = async e => {
  e.preventDefault();
  const pending = rangeClearSubmit;
  if (!pending) return;
  const form = e.currentTarget;
  const typed = form.elements.confirm.value.trim();
  if (typed !== pending.phrase) {
    $('rangeClearStatus').textContent = `请准确输入“${pending.phrase}”后再确认。`;
    return;
  }
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  try {
    await pending.submit();
    $('rangeClearDialog').close();
    await pending.onDone();
  } catch (err) {
    $('rangeClearStatus').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
