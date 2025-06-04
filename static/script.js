// Abhi ke liye koi complex JS nahi, bas search filter seller ya license ke liye

function filterLicenses() {
    const input = document.getElementById('searchInput');
    const filter = input.value.toUpperCase();
    const licenses = document.querySelectorAll('.license-card');

    licenses.forEach(card => {
        const key = card.getAttribute('data-key');
        if (key.toUpperCase().indexOf(filter) > -1) {
            card.style.display = "";
        } else {
            card.style.display = "none";
        }
    });
}

function filterSellers() {
    const input = document.getElementById('sellerSearchInput');
    const filter = input.value.toUpperCase();
    const sellers = document.querySelectorAll('.seller-card');

    sellers.forEach(card => {
        const username = card.querySelector('span').innerText.toUpperCase();
        if (username.indexOf(filter) > -1) {
            card.style.display = "";
        } else {
            card.style.display = "none";
        }
    });
}
