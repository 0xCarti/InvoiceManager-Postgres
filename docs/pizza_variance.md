# Pizza location terminal vs. physical variance

The IdealPOS export for the Pizza stand (`sales(1).xls`) shows 160 units sold for the location, including a single unit of **591ml 7-Up**, which contributes $4.50 of gross revenue. The gross column for the location totals $903.00, matching the terminal sales total shown in the app.

Physical sales in the event close-out report are calculated from the stand-sheet counts using the formula:

```
opening + transferred_in + adjustments - transferred_out - closing - eaten - spoiled
```

With the current stand-sheet data, every column for the 591ml 7-Up row is zero, so the formula above yields zero physical units even though the terminal file recorded one sale. That single missing bottle accounts for the $4.50 gap between the terminal ($903.00) and physical ($898.50) tallies for the Pizza location.

To clear the variance, update the stand sheet so that the inventory movement for the 591ml 7-Up reflects the bottle that was sold (for example, by entering the correct opening count or transfer and closing count). Once the stand-sheet inputs match the terminal movement, the physical total will increase by one unit and the variance will disappear.
