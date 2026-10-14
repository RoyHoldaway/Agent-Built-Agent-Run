// Hook for emailing/texting after a booking. Left as a stub so a client can plug in
// Resend / Postmark / Twilio later without touching the booking flow.
export interface BookingInfo {
  id: number; service: string; date: string; start: string; end: string;
  name: string; email: string; phone: string; notes: string;
}

export async function onBookingCreated(b: BookingInfo): Promise<void> {
  console.log(`[booking] #${b.id} ${b.service} ${b.date} ${b.start}-${b.end} ${b.name} <${b.email}>`);
}

export async function onBookingCancelled(b: BookingInfo): Promise<void> {
  console.log(`[booking cancelled] #${b.id} ${b.date} ${b.start} ${b.name}`);
}
