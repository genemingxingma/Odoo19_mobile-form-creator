/** @odoo-module **/

document.addEventListener("dblclick", (evt) => {
    const answerWrap = evt.target.closest(".mform-back-collapsible-answer");
    if (answerWrap) {
        answerWrap.classList.toggle("is-expanded");
        return;
    }
    const valueCell = evt.target.closest('td[data-name="value_text"]');
    if (valueCell) {
        valueCell.classList.toggle("mform-expanded");
    }
});
