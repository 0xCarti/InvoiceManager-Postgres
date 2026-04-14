(function () {
    const root = document.getElementById("signage-player");
    if (!root) {
        return;
    }

    const manifestUrl = root.dataset.manifestUrl;
    const heartbeatUrl = root.dataset.heartbeatUrl;
    const displayTitle = document.getElementById("display-title");
    const displayLocation = document.getElementById("display-location");
    const playlistName = document.getElementById("playlist-name");
    const slideSource = document.getElementById("slide-source");
    const menuName = document.getElementById("menu-name");
    const menuDescription = document.getElementById("menu-description");
    const menuGrid = document.getElementById("menu-grid");
    const slideTimer = document.getElementById("slide-timer");
    const playerStatus = document.getElementById("player-status");

    let slides = [];
    let currentIndex = 0;
    let rotationHandle = null;

    function formatPrice(value) {
        const number = Number(value || 0);
        return "$" + number.toFixed(2);
    }

    function clearRotation() {
        if (rotationHandle) {
            window.clearTimeout(rotationHandle);
            rotationHandle = null;
        }
    }

    function renderEmpty(message) {
        menuGrid.innerHTML = '<div class="empty-state">' + message + "</div>";
    }

    function renderSlide(slide) {
        const menu = slide.menu || {};
        slideSource.textContent = slide.source_type === "location_menu"
            ? "Location Menu"
            : "Specific Menu";
        menuName.textContent = menu.name || "No menu assigned";
        menuDescription.textContent = menu.description || "";
        slideTimer.textContent = "Showing for " + slide.duration_seconds + " seconds";

        if (!slide.products || !slide.products.length) {
            renderEmpty("No products are available for this menu yet.");
            return;
        }

        const cards = slide.products.map(function (product) {
            return (
                '<article class="menu-item">' +
                    '<div class="menu-item-name">' + product.name + "</div>" +
                    '<div class="menu-item-price">' + formatPrice(product.price) + "</div>" +
                "</article>"
            );
        });
        menuGrid.innerHTML = cards.join("");
    }

    function scheduleNextSlide() {
        clearRotation();
        if (!slides.length) {
            return;
        }
        const currentSlide = slides[currentIndex];
        rotationHandle = window.setTimeout(function () {
            currentIndex = (currentIndex + 1) % slides.length;
            renderSlide(slides[currentIndex]);
            scheduleNextSlide();
        }, Math.max(Number(currentSlide.duration_seconds || 15), 5) * 1000);
    }

    function applyManifest(manifest) {
        const display = manifest.display || {};
        const playlist = manifest.playlist || {};
        slides = Array.isArray(manifest.slides) ? manifest.slides : [];
        currentIndex = 0;

        displayTitle.textContent = display.name || displayTitle.textContent;
        displayLocation.textContent = display.location_name || "";
        playlistName.textContent = playlist.name
            ? playlist.name
            : "Location Menu Fallback";
        playerStatus.textContent = "Last refresh: " + new Date().toLocaleTimeString();

        if (!slides.length) {
            slideSource.textContent = "No Slides";
            menuName.textContent = "Nothing Scheduled";
            menuDescription.textContent = "";
            slideTimer.textContent = "Awaiting playlist data";
            renderEmpty("No playlist items are available for this display.");
            clearRotation();
            return;
        }

        renderSlide(slides[currentIndex]);
        scheduleNextSlide();
    }

    async function fetchManifest() {
        const response = await fetch(manifestUrl, { cache: "no-store" });
        if (!response.ok) {
            throw new Error("Manifest request failed");
        }
        const manifest = await response.json();
        applyManifest(manifest);
    }

    async function sendHeartbeat() {
        try {
            await fetch(heartbeatUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
                keepalive: true,
            });
        } catch (_error) {
            playerStatus.textContent = "Heartbeat failed";
        }
    }

    async function refresh() {
        try {
            await fetchManifest();
        } catch (_error) {
            playerStatus.textContent = "Unable to load playlist";
            renderEmpty("The player could not load its playlist.");
        }
    }

    refresh();
    sendHeartbeat();
    window.setInterval(refresh, 60000);
    window.setInterval(sendHeartbeat, 30000);
}());
