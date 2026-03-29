document.addEventListener("DOMContentLoaded", () => {
    const menuToggle = document.getElementById("menuToggle");
    if (menuToggle) {
        menuToggle.addEventListener("click", () => {
            document.body.classList.toggle("nav-open");
        });
    }

    document.querySelectorAll(".nav-link").forEach((link) => {
        link.addEventListener("click", () => {
            document.body.classList.remove("nav-open");
        });
    });

    const searchInput = document.getElementById("searchInput");
    const filterButtons = document.querySelectorAll(".filter-btn");
    if (searchInput) {
        searchInput.addEventListener("input", applyProductFilters);
    }
    filterButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            filterButtons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            applyProductFilters();
        });
    });
});

function getActiveStockFilter() {
    const active = document.querySelector(".filter-btn.active");
    return active ? active.dataset.stockFilter : "all";
}

function applyProductFilters() {
    const searchInput = document.getElementById("searchInput");
    const query = searchInput ? searchInput.value.toLowerCase().trim() : "";
    const stockFilter = getActiveStockFilter();

    document.querySelectorAll(".product-row").forEach((row) => {
        const rowText = row.textContent.toLowerCase();
        const rowStock = row.dataset.stockState || "in";

        const matchesSearch = !query || rowText.includes(query);
        const matchesStock = stockFilter === "all" || stockFilter === rowStock;

        row.style.display = matchesSearch && matchesStock ? "" : "none";
    });
}

function fillForm(barcode, name, price, weight, category, stock) {
    const barcodeInput = document.getElementById("barcode");
    const nameInput = document.getElementById("name");
    const priceInput = document.getElementById("price");
    const weightInput = document.getElementById("weight_grams");
    const categoryInput = document.getElementById("category");
    const stockInput = document.getElementById("stock");

    if (!barcodeInput) {
        return;
    }

    barcodeInput.value = barcode;
    nameInput.value = name;
    priceInput.value = price;
    weightInput.value = weight;
    categoryInput.value = category;
    stockInput.value = stock;

    const form = document.getElementById("addForm");
    if (form) {
        form.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    nameInput.focus();
}

function clearForm() {
    const form = document.getElementById("addForm");
    if (!form) {
        return;
    }
    form.reset();
}
