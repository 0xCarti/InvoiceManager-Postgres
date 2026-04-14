(function () {
    var root = document.getElementById("signage-player");
    if (!root) {
        return;
    }

    var manifestUrl = root.getAttribute("data-manifest-url") || "";
    var heartbeatUrl = root.getAttribute("data-heartbeat-url") || "";
    var displayTitle = document.getElementById("display-title");
    var displayLocation = document.getElementById("display-location");
    var playlistName = document.getElementById("playlist-name");
    var slideSource = document.getElementById("slide-source");
    var menuName = document.getElementById("menu-name");
    var menuDescription = document.getElementById("menu-description");
    var menuGrid = document.getElementById("menu-grid");
    var slideTimer = document.getElementById("slide-timer");
    var playerStatus = document.getElementById("player-status");

    var slides = [];
    var currentIndex = 0;
    var rotationHandle = null;

    function setStatus(message) {
        if (playerStatus) {
            playerStatus.textContent = message;
        }
    }

    function setText(node, value) {
        if (node) {
            node.textContent = value;
        }
    }

    function isArray(value) {
        return Object.prototype.toString.call(value) === "[object Array]";
    }

    function formatPrice(value) {
        var number = Number(value || 0);
        if (isNaN(number)) {
            number = 0;
        }
        return "$" + number.toFixed(2);
    }

    function formatRefreshTime() {
        try {
            return new Date().toLocaleTimeString();
        } catch (_error) {
            return new Date().toString();
        }
    }

    function clearRotation() {
        if (rotationHandle) {
            window.clearTimeout(rotationHandle);
            rotationHandle = null;
        }
    }

    function renderEmpty(message) {
        if (!menuGrid) {
            return;
        }
        menuGrid.innerHTML = '<div class="empty-state">' + message + "</div>";
    }

    function renderSlide(slide) {
        var menu = slide && slide.menu ? slide.menu : {};
        var products = slide && isArray(slide.products) ? slide.products : [];
        var durationSeconds = Math.max(Number(slide && slide.duration_seconds || 15), 5);

        setText(
            slideSource,
            slide && slide.source_type === "location_menu"
                ? "Location Menu"
                : "Specific Menu"
        );
        setText(menuName, menu.name || "No menu assigned");
        setText(menuDescription, menu.description || "");
        setText(slideTimer, "Showing for " + durationSeconds + " seconds");

        if (!products.length) {
            renderEmpty("No products are available for this menu yet.");
            return;
        }

        var cards = [];
        var index;
        var product;
        for (index = 0; index < products.length; index += 1) {
            product = products[index] || {};
            cards.push(
                '<article class="menu-item">' +
                    '<div class="menu-item-name">' + (product.name || "") + "</div>" +
                    '<div class="menu-item-price">' + formatPrice(product.price) + "</div>" +
                "</article>"
            );
        }
        menuGrid.innerHTML = cards.join("");
    }

    function scheduleNextSlide() {
        var currentSlide;
        clearRotation();
        if (!slides.length) {
            return;
        }
        currentSlide = slides[currentIndex] || {};
        rotationHandle = window.setTimeout(function () {
            currentIndex = (currentIndex + 1) % slides.length;
            renderSlide(slides[currentIndex]);
            scheduleNextSlide();
        }, Math.max(Number(currentSlide.duration_seconds || 15), 5) * 1000);
    }

    function applyManifest(manifest) {
        var display = manifest && manifest.display ? manifest.display : {};
        var playlist = manifest && manifest.playlist ? manifest.playlist : {};

        slides = manifest && isArray(manifest.slides) ? manifest.slides : [];
        currentIndex = 0;

        setText(displayTitle, display.name || displayTitle.textContent || "");
        setText(displayLocation, display.location_name || "");
        setText(
            playlistName,
            playlist.name ? playlist.name : "Location Menu Fallback"
        );
        setStatus("Last refresh: " + formatRefreshTime());

        if (!slides.length) {
            setText(slideSource, "No Slides");
            setText(menuName, "Nothing Scheduled");
            setText(menuDescription, "");
            setText(slideTimer, "Awaiting playlist data");
            renderEmpty("No playlist items are available for this display.");
            clearRotation();
            return;
        }

        renderSlide(slides[currentIndex]);
        scheduleNextSlide();
    }

    function parseJson(text) {
        try {
            return JSON.parse(text);
        } catch (_error) {
            return null;
        }
    }

    function requestJson(url, onSuccess, onFailure) {
        var request = new XMLHttpRequest();
        request.open("GET", url, true);
        request.setRequestHeader("Cache-Control", "no-store");
        request.onreadystatechange = function () {
            var payload;
            if (request.readyState !== 4) {
                return;
            }
            if (request.status >= 200 && request.status < 300) {
                payload = parseJson(request.responseText);
                if (payload) {
                    onSuccess(payload);
                    return;
                }
                onFailure("Unable to parse playlist data");
                return;
            }
            onFailure("Playlist request failed (" + request.status + ")");
        };
        request.onerror = function () {
            onFailure("Playlist request failed");
        };
        request.send(null);
    }

    function sendHeartbeat() {
        var request = new XMLHttpRequest();
        request.open("POST", heartbeatUrl, true);
        request.setRequestHeader("Content-Type", "application/json");
        request.onerror = function () {
            setStatus("Heartbeat failed");
        };
        try {
            request.send("{}");
        } catch (_error) {
            setStatus("Heartbeat failed");
        }
    }

    function refresh() {
        requestJson(
            manifestUrl,
            function (manifest) {
                applyManifest(manifest);
            },
            function (message) {
                setStatus(message);
                renderEmpty("The player could not load its playlist.");
                clearRotation();
            }
        );
    }

    refresh();
    sendHeartbeat();
    window.setInterval(refresh, 60000);
    window.setInterval(sendHeartbeat, 30000);
}());
